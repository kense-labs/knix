#!/usr/bin/env python

from distutils.core import setup

setup(name='knix_firmware',
      version='0.1',
      description='Knix firmware tools',
      author='Hector Martin',
      author_email='marcan@marcan.st',
      url='https://github.com/Knix/knix-installer/',
      packages=['knix_firmware'],
      entry_points={"console_scripts": ["knix-fwextract = knix_firmware.update:main"]}
     )
