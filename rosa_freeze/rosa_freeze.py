#
# rose_freeze - the main 'ROSA Freeze' module
#
# Author: Denis Silakov <denis.silakov@rosalab.ru>
#
# Copyright (C) 2014 ROSA Company
#
# Distributed under the BSD license
#

import argparse
import os
import re
import sys
import shutil

grub_cfg_name = '/etc/default/grub'
grub_tmp_cfg_name = '/etc/default/grub.new'

#########################
# Top-level functions:
#   * enable_freeze
#   * disable_freeze
#   * get_status
#########################

'''
Enable freeze mode

Return Value:
    0 - success
    2 - freeze mode is already enabled
    3 - freeze mode was disabled but the system was not rebooted after that
    4 - failed to get UUID for the storage device
    5 - storage device is already mounted
    61 - path to the folder is not absolute
    62 - can't determine top-level parent for the folder
'''
def enable_freeze(skip_dirs, storage, folder=""):
    status = get_status()
    if status == 'enabled':
        return 2
    elif status == 'disabled_pending':
        return 3

    uuid = ""

    if storage == "folder":
        if not folder.startswith("/"):
            return 61

        if not os.path.exists(folder):
            os.system("mkdir -p " + folder)

        # Let's ensure that the folder will not be frozen
        path_components = folder.split("/")
        if not path_components[1]:
            # Can't determine top-level parent for the folder
            return 62

        if not path_components[1] in skip_dirs:
            skip_dirs.append(path_components[1])

    elif storage != "tmpfs":
        # Separate partition will be used as a storage
	uuid = _get_device_uuid(storage)
	if not uuid:
	    return 4
	storage_mnt = os.popen("findmnt -n -o TARGET " + storage).read().rstrip()
	if storage_mnt:
	    return 5

    # For safety - load aufs module
    os.system("modprobe aufs")

    # Enable aufs mounting for root in dracut
    _enable_freeze_dracut(uuid, skip_dirs, folder)

    # Make sure that aufs kernel module is loaded at boot time
    if not os.path.isfile('/etc/modules-load.d/aufs.conf'):
        aufs_conf = open('/etc/modules-load.d/aufs.conf', 'w')
        aufs_conf.write('aufs\n')
        aufs_conf.close()

    # Finally, let's enter freeze mode right now
    _enable_freeze_now(skip_dirs, storage, folder)

    return 0

'''
Disable freeze mode

Return Value:
    0 - success
    1 - freeze mode is not enabled
'''
def disable_freeze():
    status = get_status()
    if status != 'enabled':
        return 1

    # Find original root folder and mount it to /tmp/sysroot-orig
    orig_root = os.popen("findmnt -n -o SOURCE --target /tmp/sysroot-ro").read().rstrip()
    os.system("mkdir -p /tmp/sysroot-orig")
    os.system("mount -o rw " + orig_root + " /tmp/sysroot-orig")
    # Prepare for chrooting into sysroot-orig
    os.system("mount -o bind /dev /tmp/sysroot-orig/dev")
    os.system("mount -o bind /dev/pts /tmp/sysroot-orig/dev/pts")
    os.system("mount -o bind /proc /tmp/sysroot-orig/proc")
    os.system("mount -o bind /sys /tmp/sysroot-orig/sys")
    os.system("mount -o bind /run /tmp/sysroot-orig/run")
    # Update dracut options in grub config
    _disable_freeze_dracut('/tmp/sysroot-orig/')
    os.system("chroot /tmp/sysroot-orig/ update-grub2")

    # Cleanup
#    os.system("umount /tmp/sysroot-orig")
#    os.system("rm -f /tmp/sysroot-orig")

    return 0

'''
Try to merge current state to the original filesystem

Return Value:
    0 - success
    1 - freeze mode is not enabled
'''
def merge_state():
    status = get_status()
    if status != 'enabled':
        return 1

    # Find original root device and mount it to /tmp/sysroot-orig
    orig_root = os.popen("findmnt -n -o SOURCE --target /tmp/sysroot-ro").read().rstrip()
    os.system("mkdir -p /tmp/sysroot-orig")
    os.system("mount -o rw " + orig_root + " /tmp/sysroot-orig")

    # rsync current state of 'frozen' folders.
    # Note that we don't support situation when some of these folders
    # are originally mounted from different partitions
    # (e.g., /var on separate partinion is not supported)
    for d in os.listdir('/tmp/sysroot-rw/'):
        os.system("rsync -avH --delete /" + d + " /tmp/sysroot-orig")

    # Cleanup
#    os.system("umount /tmp/sysroot-orig")
#    os.system("rm -f /tmp/sysroot-orig")

    return 0

'''
Get current status of ROSA Freeze

Possible return values:
    * enabled
    * disabled
    * disabled_pending (freeze mode was disabled, but the system was not rebooted after that)
'''
def get_status():
    aufs_enabled = os.system("grep GRUB_CMDLINE_LINUX_DEFAULT " + grub_cfg_name + " | grep -q aufs_root")
    if aufs_enabled == 0:
        return 'enabled'
    else:
        aufs_mounted = os.system("findmnt --target /tmp/sysroot-rw -n >/dev/null")
        if aufs_mounted == 0:
            return 'disabled_pending'
        else:
            return 'disabled'

###########################
# Interlal functions
###########################

'''
Modify dracut parameters in grub config - drop aufs_root
'''
def _disable_freeze_dracut(chroot=""):
    grub_cfg = open(chroot + grub_cfg_name, 'r')
    grub_new_cfg = open(chroot + grub_tmp_cfg_name, 'w')

    for line in grub_cfg:
        if line.startswith('GRUB_CMDLINE_LINUX_DEFAULT'):
            line = re.sub(r' aufs_root=UUID=[\S]*([\'" ])', r'\1', line)
            line = re.sub(r' aufs_root=DIR=[\S]*([\'" ])', r'\1', line)
            line = re.sub(r' rfreeze_skip_dirs=[\S]*([\'" ])', r'\1', line)
        grub_new_cfg.write(line)
    grub_new_cfg.close()

    shutil.move(chroot + grub_tmp_cfg_name, chroot + grub_cfg_name)


'''
Enter freeze mode in a running system
'''
def _enable_freeze_now(skip_dirs, storage, folder=""):
    os.system("mkdir -p /tmp/xinos")
    os.system("mkdir -p /tmp/sysroot-rw")
    os.system("mkdir -p /tmp/sysroot-ro")
    os.system("mount -o bind / /tmp/sysroot-ro")
    if storage == "tmpfs":
	os.system("mount -n -t tmpfs tmpfs /tmp/sysroot-rw")
    elif storage == "folder":
        os.system("mount -o bind " + folder + " /tmp/sysroot-rw")
    else:
	os.system("mount -n " + storage + " /tmp/sysroot-rw")

    for d in os.listdir('/'):
        if os.path.isdir("/" + d) and d not in skip_dirs:
            os.system("rm -rf /tmp/sysroot-rw/" + d)
            if d == "root":
                os.system("mkdir -m 750 -p /tmp/sysroot-rw/" + d)
            else:
                os.system("mkdir -m 755 -p /tmp/sysroot-rw/" + d)
            os.system("mount -n -t aufs -o nowarn_perm,noatime,xino=/tmp/xinos/" + d + ".xino.aufs,dirs=/tmp/sysroot-rw/" + d + "=rw:/" + d + "=rr none /" + d)

'''
Modify dracut parameters in grub config - add aufs_root
'''
def _enable_freeze_dracut(uuid, skip_dirs, folder):
    grub_cfg = open(grub_cfg_name, 'r')
    grub_new_cfg = open(grub_tmp_cfg_name, 'w')
    dracut_skip_dirs = ":".join(skip_dirs)

    for line in grub_cfg:
        if line.startswith('GRUB_CMDLINE_LINUX_DEFAULT'):
            line = re.sub(r'([\'"]\s*)$', r' rfreeze_skip_dirs=' + dracut_skip_dirs + r' aufs_root=UUID=\1', line)
            if uuid:
                line = line.replace("aufs_root=UUID=", "aufs_root=UUID=" + uuid)
            elif folder:
                line = line.replace("aufs_root=UUID=", "aufs_root=DIR=" + folder)
        grub_new_cfg.write(line)
    grub_new_cfg.close()

    shutil.move(grub_tmp_cfg_name, grub_cfg_name)
    os.system("update-grub2")

'''
Get UUID by device name
'''
def _get_device_uuid(storage):
    uuid = os.popen("blkid " + storage + " -o udev | grep 'UUID=' | cut -f2 -d=").read().rstrip()
    return uuid