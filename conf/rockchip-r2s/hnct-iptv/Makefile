include $(TOPDIR)/rules.mk

PKG_NAME:=hnct-iptv
PKG_VERSION:=1.0
PKG_RELEASE:=1

include $(INCLUDE_DIR)/package.mk


define Package/hnct-iptv
  SECTION:=net
  CATEGORY:=Network
  TITLE:=HNCT IPTV FastAPI Service
  DEPENDS:=+python3
endef


define Package/hnct-iptv/install

	$(INSTALL_DIR) $(1)/etc/hnct-iptv
	$(CP) ./files/etc/hnct-iptv/* \
		$(1)/etc/hnct-iptv/

	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/etc/init.d/hnct-iptv \
		$(1)/etc/init.d/

	$(INSTALL_DIR) $(1)/usr/lib/python3.11/site-packages
	$(CP) ./files/usr/lib/python3.11/site-packages/* \
		$(1)/usr/lib/python3.11/site-packages/

endef


$(eval $(call BuildPackage,hnct-iptv))