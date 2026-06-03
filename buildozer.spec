[app]

# (str) Title of your application
title = 心电预警系统

# (str) Package name
package.name = aiecgnonitor

# (str) Package domain (needed for android packaging)
package.domain = org.ecg.monitor

# (str) Source code directory
source.dir = .

# (list) Source files to include (file extensions)
source.include_exts = py,png,jpg,kv,atlas,ttf

# (str) Application version
version = 1.0

# (list) Application requirements
requirements = python3,kivy,pyserial,pyjnius,android

# (str) Supported orientations
orientation = portrait

# (bool) Use fullscreenmode or not
fullscreen = 0

# ==============================================================================
# Android specific configuration
# ==============================================================================

# (list) Permissions (这里已为你补齐了所有蓝牙、定位和文件存储所需的全部权限)
android.permissions = BLUETOOTH, BLUETOOTH_ADMIN, BLUETOOTH_CONNECT, BLUETOOTH_SCAN, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE

# (int) Target device API
android.api = 33

# (int) Minimum API required (保持 24 以兼容 numpy 计算库)
android.minapi = 24

# (list) The Android archs to build for
android.archs = armeabi-v7a, arm64-v8a

# (bool) Enable AndroidX support
android.enable_androidx = True

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with compiler output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1

