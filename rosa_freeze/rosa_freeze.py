#
# rose_freeze - the main 'ROSA Freeze' module
#
# Author: Denis Silakov <denis.silakov@rosalab.ru>
#
# Copyright (C) 2014-2015 ROSA Company
#
# Distributed under the BSD license
#

import argparse
import os
import re
import sys
import shutil
import time
import subprocess

grub_cfg_name = '/etc/default/grub'
grub_tmp_cfg_name = '/etc/default/grub.new'

rfreeze_config = '/etc/rfreeze.conf'
modulename = 'overlay'

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
    99 - smth went wrong during 'os.system' run
'''
def enable_freeze(skip_dirs, storage, folder=""):
    status = get_status()
    if status == 'enabled':
        return 2
    elif status == 'disabled_pending':
        return 3

    uuid = ""

    skip_dirs.extend(_detect_folders_to_skip(skip_dirs))

    if storage == "folder":
        if not folder.startswith("/"):
            return 61

        if not os.path.exists(folder):
            if os.system("mkdir -p " + folder):
                return 99

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

    # For safety - load module
    if os.system("modprobe " + modulename):
        return 99

    # Enable filesystem mounting for root in dracut
    _enable_freeze_dracut(uuid, skip_dirs, folder)

    # Make sure that kernel module is loaded at boot time
    if not os.path.isfile('/etc/modules-load.d/' + modulename + '.conf'):
        union_conf = open('/etc/modules-load.d/' + modulename + '.conf', 'w')
        union_conf.write(modulename + '\n')
        union_conf.close()

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

Parameters:
    backup_folder - if not empty, specifies a fodler to store backups of modified/removed files

Return Value:
    0 - success
    1 - freeze mode is not enabled
    99 - smth went wrong during os.system run
'''
def merge_state(backup_folder=""):
    status = get_status()
    if status != 'enabled':
        return 1

    # Find original root device and mount it to /tmp/sysroot-orig
    orig_root = os.popen("findmnt -n -o SOURCE --target /tmp/sysroot-ro").read().rstrip()
    if os.system("mkdir -p /tmp/sysroot-orig"):
        return 99
    if os.system("mount -o rw " + orig_root + " /tmp/sysroot-orig"):
        return 99

    backup_params = ""
    if backup_folder > "":
        backup_params = "--backup --backup-dir=" + backup_folder

    # rsync current state of 'frozen' folders.
    # Note that we don't support situation when some of these folders
    # are originally mounted from different partitions
    # (e.g., /var on separate partinion is not supported)
    for d in os.listdir('/tmp/sysroot-rw/'):
        if os.system("rsync -avH --delete " + backup_params + " /" + d + " /tmp/sysroot-orig"):
            return 99

    # Cleanup
#    os.system("umount /tmp/sysroot-orig")
#    os.system("rm -f /tmp/sysroot-orig")

    return 0

'''
Get current status of ROSA Freeze

Possible return values:
    * 'enabled'
    * 'disabled'
    * 'disabled_pending' (freeze mode was disabled, but the system was not rebooted after that)
'''
def get_status():
    union_enabled = os.system("grep GRUB_CMDLINE_LINUX_DEFAULT " + grub_cfg_name + " | grep -q " + modulename + "_root")
    union_mounted = os.system("findmnt --target /tmp/sysroot-rw -n >/dev/null")
    if union_enabled == 0 and union_mounted == 0:
        return 'enabled'
    else:
        if union_mounted == 0:
            return 'disabled_pending'
        else:
            return 'disabled'

'''
Create restore point

Return Value:
    0 - success
    1 - freeze mode is not enabled
    2 - no folder is specified to store restore point and tmpfs is used to store changes;
        we refuse to create restore points in tmpfs
'''
def create_restore_point(folder=""):
    status = get_status()
    if status != 'enabled':
        return 1

    storage_type = os.popen("findmnt -n -o SOURCE --target /tmp/sysroot-rw").read().rstrip()
    if storage_type == "tmpfs":
        return 2

    if folder == "":
        folder = "/tmp/sysroot-orig/restore_points"
    else:
	folder = "/tmp/sysroot-orig" + folder

    # Append current timestamp to folder
    folder = folder + "/" + str(int(time.time()))

    res = merge_state(folder)
    exit(res)

'''
Get available restore points
'''
def list_restore_points(folder=""):
    points = []
    try:
        for d in os.listdir(folder):
            if os.path.isdir(folder + "/" + d):
                points.append(d)
    except:
        # Just return empty array if we can't get a list of restore points
        # (e.g., if 'folder' doesn't exist or can't be read)
        pass

    return points

'''
Rollback to a given restore point

Parameters:
    folder - a fodler with backups of modified/removed files

Return Value:
    0 - success
    1 - invalid restore point
    99 - smth went wrong during os.system run
'''
def rollback_to_point(point, folder=""):
    points = list_restore_points(folder)
    if point not in points:
        return 1

    for d in os.listdir(folder + '/' + point):
        if os.system("rsync -avH --delete " + folder + "/" + point + "/" + d + " /" + d):
            return 99

    return 0


###########################
# Interlal functions
###########################

'''
Modify dracut parameters in grub config - drop root
'''
def _disable_freeze_dracut(chroot=""):
    try:
        # /etc is protected
        grub_cfg = open(chroot + grub_cfg_name, 'r')
        grub_new_cfg = open(chroot + grub_tmp_cfg_name, 'w')
    except:
        # /etc is skipped
        grub_cfg = open(grub_cfg_name, 'r')
        grub_new_cfg = open(grub_tmp_cfg_name, 'w')

    for line in grub_cfg:
        if line.startswith('GRUB_CMDLINE_LINUX_DEFAULT'):
            line = re.sub(r'%s_root=UUID=[\S]*([\'" ])' % modulename, r'\1', line)
            line = re.sub(r'%s_root=DIR=[\S]*([\'" ])' % modulename, r'\1', line)
            line = re.sub(r' rfreeze_skip_dirs=[\S]*([\'" ])', r'\1', line)
        grub_new_cfg.write(line)
    grub_new_cfg.close()

    try:
        # /etc is protected
        shutil.move(chroot + grub_tmp_cfg_name, chroot + grub_cfg_name)
    except:
        # /etc is skipped
        shutil.move(grub_tmp_cfg_name, grub_cfg_name)


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

	    if modulename == 'aufs':
            	if os.system("mount -n -t aufs -o nowarn_perm,noatime,xino=/tmp/xinos/" + d + ".xino.aufs,dirs=/tmp/sysroot-rw/" + d + "=rw:/" + d + "=rr none /" + d):
                    return 99
	    elif modulename == 'overlay':
	    	if os.system("mkdir -p /tmp/sysroot-rw/workdir/" + d + "; mount -n -t overlay -o upperdir=/tmp/sysroot-rw/" + d + ",lowerdir=/" + d + ",workdir=/tmp/sysroot-rw/workdir/" + d + " none /" + d):
                    return 99

'''
Modify dracut parameters in grub config - add root
'''
def _enable_freeze_dracut(uuid, skip_dirs, folder):
    grub_cfg = open(grub_cfg_name, 'r')
    grub_new_cfg = open(grub_tmp_cfg_name, 'w')
    dracut_skip_dirs = ":".join(skip_dirs)

    # Check if initrd already contains union-mount
    lsinitrd = subprocess.check_output(('lsinitrd'))
    union_mount_present = lsinitrd.find("union_mount") >= 0
    if not union_mount_present:
        os.system('dracut -f /boot/initrd-$(uname -r).img $(uname -r)')

    for line in grub_cfg:
        if line.startswith('GRUB_CMDLINE_LINUX_DEFAULT'):
            line = re.sub(r'([\'"]\s*)$', r' rfreeze_skip_dirs=%s %s_root=UUID=\1' % (dracut_skip_dirs,modulename), line)
            if uuid:
                line = line.replace(modulename + "_root=UUID=",modulename +  "_root=UUID=" + uuid)
            elif folder:
                line = line.replace(modulename + "_root=UUID=", modulename +  "_root=DIR=" + folder)
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

'''
Detect which top-level folders are used as a mount points.
Return a list of such folders which are not yet included in skip_dirs
'''
def _detect_folders_to_skip(skip_dirs):
    more_dirs = []
    for d in os.listdir('/'):
        if os.path.isdir("/" + d) and d not in skip_dirs:
            dir_mnt = os.popen("findmnt -n -o TARGET /" + d).read().rstrip()
            if dir_mnt:
                more_dirs.append(d)
                print(_("NOTE: '%s' folder is mounted from another partition, it will not be frozen") % d)

    return more_dirs
