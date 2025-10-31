#!/usr/local/bin/python3
# 用户插件目录存储在存储卷中，会在启动时覆盖掉容器的默认插件目录。此脚本用于默认插件更新后或麦麦首次启动时为用户自动安装默认插件到存储卷中
# 如果用户主动删除插件且插件无更新，则不会再次安装。插件状态保存在/MaiMBot/data/plugins/.installed-setup-plugins文件中
# 此脚本应当挂载进初始化容器中，从/MaiMBot工作路径开始运行。初始化容器的镜像同core容器，初始化容器中应挂载core存储卷的数据到/MaiMBot/data
import os
import shutil
import hashlib
from datetime import datetime

SRC_DIR = '/MaiMBot/plugins'
DST_DIR = '/MaiMBot/data/plugins'
STATUS_FILE = f'{DST_DIR}/.installed-setup-plugins'
BAK_DIR = '/MaiMBot/data/plugins-backup'
CURRENT_TIME = datetime.now().strftime('%Y%m%d%H%M%S')

def hash_dir_file(path: str):
    """计算目录/文件的SHA256，用于判断是否发生变化"""
    def hash_file(_file_path: str):
        _h = hashlib.sha256()
        with open(_file_path, 'rb') as _f:
            for _chunk in iter(lambda: _f.read(8192), b''):
                _h.update(_chunk)
        return _h.hexdigest()

    if os.path.isfile(path):
        return hash_file(path)

    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        for filename in sorted(files):
            filepath = os.path.join(root, filename)
            relpath = os.path.relpath(filepath, path)
            file_hash = hash_file(filepath)
            h.update(relpath.encode('utf-8'))
            h.update(file_hash.encode('utf-8'))
    return h.hexdigest()

def copy_plugin(plugin: str):
    """复制插件，如果插件已存在则备份旧的插件然后用新的插件覆盖"""
    src = os.path.join(SRC_DIR, plugin)
    if not os.path.exists(src):
        raise FileNotFoundError(f"File not found: {src}")

    dst = os.path.join(DST_DIR, plugin)
    if os.path.exists(dst):
        print(f"\t\tWarning: Old version of plugin '{plugin}' already exists. "
              f"Old plugin will be moved to '{BAK_DIR}/{CURRENT_TIME}/{plugin}'. "
              f"Remember to re-edit config of this plugin.")
        if not os.path.exists(os.path.join(BAK_DIR, CURRENT_TIME)):
            os.makedirs(os.path.join(BAK_DIR, CURRENT_TIME))
        if os.path.isdir(dst):
            shutil.copytree(dst, os.path.join(BAK_DIR, CURRENT_TIME, plugin))
            shutil.rmtree(dst)
        else:
            shutil.copy2(dst, os.path.join(BAK_DIR, CURRENT_TIME))
            os.remove(dst)

    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, DST_DIR)

setup_plugins = {plugin: hash_dir_file(plugin) for plugin in os.listdir(SRC_DIR)}
installed_plugins = {}
to_install_plugins = {}

print(f"[SetupPlugins] Default plugin, which has been updated or never been installed, "
      f"will be installed in this init container.")
if os.path.exists(STATUS_FILE) and os.path.isfile(STATUS_FILE):
    print(f"[SetupPlugins] Reading status file: '{STATUS_FILE}'...")
    with open(STATUS_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        for line in lines:
            if line == '':
                continue
            plugin = line.strip().split(':')
            installed_plugins[plugin[0]] = plugin[1]
    print(f"[SetupPlugins] Found {len(installed_plugins)} default plugins which used to be installed:")
    for plugin in installed_plugins.keys():
        print(f'\t{plugin}')
else:
    print(f"[SetupPlugins] No status file found. Status file '{STATUS_FILE}' will be created. "
          f"All default plugins will be installed now.")

print(f"[SetupPlugins] Checking plugins...")
for plugin, sha256 in setup_plugins.items():
    if (plugin not in installed_plugins) or (sha256 != installed_plugins[plugin]):
        print(f"\tFound default plugin to install: '{plugin}'. Installing...")
        copy_plugin(plugin)
        installed_plugins[plugin] = sha256

with open(STATUS_FILE, 'w', encoding='utf-8') as f:
    f.write('\n'.join(sorted([f'{plugin}:{sha256}' for plugin, sha256 in installed_plugins.items()])))

print(f"[SetupPlugins] Default plugin checking done. Status saved to '{STATUS_FILE}'.")
