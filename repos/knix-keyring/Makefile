V=20220223

PREFIX = /usr/local

install:
	install -dm755 $(DESTDIR)$(PREFIX)/share/pacman/keyrings/
	install -m0644 knix{.gpg,-trusted,-revoked} $(DESTDIR)$(PREFIX)/share/pacman/keyrings/

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/share/pacman/keyrings/knix{.gpg,-trusted,-revoked}
	rmdir -p --ignore-fail-on-non-empty $(DESTDIR)$(PREFIX)/share/pacman/keyrings/

dist:
	git archive --format=tar --prefix=knix-keyring-$(V)/ $(V) | gzip -9 > knix-keyring-$(V).tar.gz
	gpg --detach-sign --use-agent knix-keyring-$(V).tar.gz

.PHONY: install uninstall dist upload
