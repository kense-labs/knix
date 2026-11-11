// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Knix driver GEM object implementation
//!
//! Basic wrappers and adaptations between generic GEM shmem objects and this driver's
//! view of what a GPU buffer object is. It is in charge of keeping track of all mappings for
//! each GEM object so we can remove them when a client (File) or a Vm are destroyed, as well as
//! implementing RTKit buffers on top of GEM objects for firmware use.

use kernel::{
    drm::{
        self,
        gem::{
            shmem::{
                self,
                VMapOwned, //
            },
            BaseObject,
            DriverObject, //
        }, //
    },
    error::Result,
    prelude::*,
    sync::aref::ARef,
    uapi, //
};

use core::ops::Range;
use core::sync::atomic::{
    AtomicU64,
    Ordering, //
};

use crate::{
    debug::*,
    driver::{
        KnixDrmDevice,
        KnixDrmDriver, //
    },
    file,
    mmu,
    util::*, //
};

const DEBUG_CLASS: DebugFlags = DebugFlags::Gem;

/// Represents the inner data of a GEM object for this driver.
#[pin_data]
pub(crate) struct KnixObject {
    /// ID for debug
    id: u64,
    /// Object creation flags.
    flags: u32,
    /// Whether this object can be exported.
    exportable: bool,
    /// Whether this is a kernel-created object.
    kernel: bool,
}

/// Type alias for the shmem GEM object type for this driver.
pub(crate) type Object = shmem::Object<KnixObject>;

unsafe impl Send for KnixObject {}
unsafe impl Sync for KnixObject {}

/// A shared reference to a GEM object for this driver.
pub(crate) struct ObjectRef {
    /// The underlying GEM object reference
    pub(crate) gem: ARef<Object>,
    /// The kernel-side VMap of this object, if needed
    // vmap: Option<VMapOwned::<u8, KnixObject>>,
    vmap: Option<VMapOwned<KnixObject, 0>>,
}

crate::no_debug!(ObjectRef);

static GEM_ID: AtomicU64 = AtomicU64::new(0);

impl ObjectRef {
    /// Create a new wrapper for a raw GEM object reference.
    pub(crate) fn new(gem: ARef<Object>) -> ObjectRef {
        ObjectRef { gem, vmap: None }
    }

    /// Return the `VMap` for this object, creating it if necessary.
    pub(crate) fn vmap(&mut self) -> Result<&shmem::VMapOwned<KnixObject, 0>> {
        if self.vmap.is_none() {
            self.vmap = Some(self.gem.owned_vmap::<0>()?);
        }
        self.vmap.as_ref().ok_or(EINVAL)
    }

    /// Returns the size of an object in bytes
    pub(crate) fn size(&self) -> usize {
        self.gem.size()
    }

    /// Maps an object into a given `Vm` at any free address within a given range.
    pub(crate) fn map_into_range(
        &mut self,
        vm: &crate::mmu::Vm,
        range: Range<u64>,
        alignment: u64,
        prot: mmu::Prot,
        guard: bool,
    ) -> Result<crate::mmu::KernelMapping> {
        // Only used for kernel objects now
        if !self.gem.kernel {
            return Err(EINVAL);
        }
        vm.map_in_range(&self.gem, 0..self.gem.size(), alignment, range, prot, guard)
    }

    /// Maps a range within an object into a given `Vm` at any free address within a given range.
    pub(crate) fn map_range_into_range(
        &mut self,
        vm: &crate::mmu::Vm,
        obj_range: Range<usize>,
        range: Range<u64>,
        alignment: u64,
        prot: mmu::Prot,
        guard: bool,
    ) -> Result<crate::mmu::KernelMapping> {
        if obj_range.end > self.gem.size() {
            return Err(EINVAL);
        }
        if self.gem.flags & uapi::drm_knix_gem_flags_DRM_KNIX_GEM_VM_PRIVATE != 0
            && vm.is_extobj(&*self.gem)
        {
            return Err(EINVAL);
        }
        vm.map_in_range(&self.gem, obj_range, alignment, range, prot, guard)
    }

    /// Maps an object into a given `Vm` at a specific address.
    ///
    /// Returns Err(ENOSPC) if the requested address is already busy.
    pub(crate) fn map_at(
        &mut self,
        vm: &crate::mmu::Vm,
        addr: u64,
        prot: mmu::Prot,
        guard: bool,
    ) -> Result<crate::mmu::KernelMapping> {
        if self.gem.flags & uapi::drm_knix_gem_flags_DRM_KNIX_GEM_VM_PRIVATE != 0
            && vm.is_extobj(&*self.gem)
        {
            return Err(EINVAL);
        }
        vm.map_at(addr, self.gem.size(), &self.gem, prot, guard)
    }
}

pub(crate) struct KnixObjConfig {
    flags: u32,
    exportable: bool,
    kernel: bool,
}

/// Create a new kernel-owned GEM object.
pub(crate) fn new_kernel_object(dev: &KnixDrmDevice, size: usize) -> Result<ObjectRef> {
    let gem = shmem::Object::<KnixObject>::new(
        dev,
        align(size, mmu::UAT_PGSZ),
        shmem::ObjectConfig::<KnixObject> {
            map_wc: false,
            parent_resv_obj: None,
        },
        KnixObjConfig {
            flags: 0,
            exportable: false,
            kernel: true,
        },
    )?;

    mod_pr_debug!("KnixObject new kernel object id={}\n", gem.id);
    Ok(ObjectRef::new(gem))
}

/// Create a new user-owned GEM object with the given flags.
pub(crate) fn new_object(
    dev: &KnixDrmDevice,
    size: usize,
    flags: u32,
    parent_object: Option<&shmem::Object<KnixObject>>,
) -> Result<ARef<Object>> {
    if (flags & uapi::drm_knix_gem_flags_DRM_KNIX_GEM_VM_PRIVATE != 0) != parent_object.is_some()
    {
        return Err(EINVAL);
    }

    let gem = shmem::Object::<KnixObject>::new(
        dev,
        align(size, mmu::UAT_PGSZ),
        shmem::ObjectConfig::<KnixObject> {
            map_wc: flags & uapi::drm_knix_gem_flags_DRM_KNIX_GEM_WRITEBACK == 0,
            parent_resv_obj: parent_object,
        },
        KnixObjConfig {
            flags,
            exportable: parent_object.is_none(),
            kernel: false,
        },
    )?;

    mod_pr_debug!("KnixObject new user object: id={}\n", gem.id);
    Ok(gem)
}

#[vtable]
impl DriverObject for KnixObject {
    type Driver = KnixDrmDriver;
    type Args = KnixObjConfig;

    const HAS_EXPORT: bool = true;

    /// Callback to create the inner data of a GEM object
    fn new(_dev: &KnixDrmDevice, _size: usize, args: Self::Args) -> impl PinInit<Self, Error> {
        let id = GEM_ID.fetch_add(1, Ordering::Relaxed);
        mod_pr_debug!("KnixObject::new id={}\n", id);
        try_pin_init!(KnixObject {
            id,
            flags: args.flags,
            exportable: args.exportable,
            kernel: args.kernel,
        })
    }

    fn create_imported(_dev: &KnixDrmDevice, _size: usize) -> impl PinInit<Self, Error> {
        let id = GEM_ID.fetch_add(1, Ordering::Relaxed);
        mod_pr_debug!("KnixObject::create_imported id={}\n", id);
        try_pin_init!(Self {
            id,
            flags: 0,
            exportable: false,
            kernel: false,
        })
    }

    /// Callback to drop all mappings for a GEM object owned by a given `File`
    fn close(obj: &<Self::Driver as drm::Driver>::Object, file: &drm::gem::DriverFile<Self>) {
        // fn close(obj: &Object, file: &DrmFile) {
        mod_pr_debug!("KnixObject::close id={}\n", obj.id);
        if file::File::unbind_gem_object(file, obj).is_err() {
            pr_err!("KnixObject::close: Failed to unbind GEM object\n");
        }
    }

    /// Optional handle for exporting a gem object.
    fn export(
        obj: &<Self::Driver as drm::Driver>::Object,
        flags: u32,
    ) -> Result<drm::gem::DmaBuf<Object>> {
        if !obj.exportable {
            return Err(EINVAL);
        }

        obj.prime_export(flags)
    }
}
