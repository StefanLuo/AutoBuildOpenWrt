#!/bin/bash
# Log file for debugging
LOGFILE="/tmp/uci-defaults-log.txt"
echo "Starting 99-custom.sh at $(date)" >> $LOGFILE
# yml 传入的路由器型号 PROFILE
echo "Building for profile: $PROFILE"
# yml 传入的固件大小 ROOTFS_PARTSIZE
echo "Building for ROOTFS_PARTSIZE: $ROOTFS_PARTSIZE"
# yml 传入的LAN IP地址 LAN_IP
echo "Building for LAN_IP: $LAN_IP"

mkdir -p  /home/build/openwrt/files/etc/config

# 创建lan ip配置文件 yml传入环境变量LAN_IP 写入配置文件 供99-custom.sh读取
echo "Create lan-settings"
cat << EOF > /home/build/openwrt/files/etc/config/lan-settings
lan_ip=${LAN_IP}
EOF

echo "cat lan-settings"
cat /home/build/openwrt/files/etc/config/lan-settings

# 创建pppoe配置文件 yml传入环境变量ENABLE_PPPOE等 写入配置文件 供99-custom.sh读取
echo "Create pppoe-settings"
cat << EOF > /home/build/openwrt/files/etc/config/pppoe-settings
enable_pppoe=${ENABLE_PPPOE}
pppoe_account=${PPPOE_ACCOUNT}
pppoe_password=${PPPOE_PASSWORD}
EOF

echo "cat pppoe-settings"
cat /home/build/openwrt/files/etc/config/pppoe-settings

# 输出调试信息
echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting build process..."


# 定义所需安装的包列表 下列插件你都可以自行删减
# PACKAGES=""
# PACKAGES="$PACKAGES curl"
# PACKAGES="$PACKAGES luci-i18n-diskman-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-package-manager-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-firewall-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-filebrowser-zh-cn"
# PACKAGES="$PACKAGES luci-app-argon-config"
# PACKAGES="$PACKAGES luci-i18n-argon-config-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-ttyd-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-passwall-zh-cn"
# PACKAGES="$PACKAGES luci-app-openclash"
# PACKAGES="$PACKAGES luci-i18n-homeproxy-zh-cn"
# PACKAGES="$PACKAGES openssh-sftp-server"
# PACKAGES="$PACKAGES luci-i18n-dockerman-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-msd_lite-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-upnp-zh-cn"
# PACKAGES="$PACKAGES luci-i18n-vlmcsd-zh-cn"
# PACKAGES="$PACKAGES luci-proto-wireguard"
# 增加几个必备组件 方便用户安装iStore
# PACKAGES="$PACKAGES fdisk"
# PACKAGES="$PACKAGES script-utils"
# PACKAGES="$PACKAGES luci-i18n-samba4-zh-cn"

# 构建镜像
echo "$(date '+%Y-%m-%d %H:%M:%S') - Building image with the following packages:"
echo "$PACKAGES"

make image PROFILE=$PROFILE PACKAGES="$PACKAGES" FILES="/home/build/openwrt/files" ROOTFS_PARTSIZE=$ROOTFS_PARTSIZE

if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Error: Build failed!"
    exit 1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') - Build completed successfully."
