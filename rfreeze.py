#!/usr/bin/python
#
# rfreeze - Manipulate 'ROSA Freeze' features
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

# List of root folders that are not mounted over aufs
# This set should be kept in sync with SKIP_DIRS in /usr/lib/dracut/modules.d/99aufs-mount/aufs-mount.sh
skip_dirs = ['dev', 'home', 'lost+found', 'proc', 'run', 'sys', 'tmp']

def parse_command_line():
    global command_line
    parser = argparse.ArgumentParser(description='ROSA Freeze Configuration Tool')
#    parser.add_argument('-v', '--verbose', action='store_true', help='be verbose, display even debug messages')
#    parser.add_argument('-q', '--quiet', action='store_true', help='Do not display info messages')
    subparsers = parser.add_subparsers(title='command')

    # Enable freeze
    parser_enable = subparsers.add_parser('enable', help='Enable Freeze mode')
    parser_enable.add_argument('skip_dir', action='store', nargs='*', help='Additional folders to be skipped')
    parser_enable.set_defaults(func=enable_freeze)

    # Disable freeze
    parser_disable = subparsers.add_parser('disable', help='Disable Freeze mode')
    parser_disable.set_defaults(func=disable_freeze)

    # Get status
    parser_status = subparsers.add_parser('status', help='Get ROSA Freeze status')
    parser_status.set_defaults(func=print_status)

    # Merge current system state into the original one
    parser_merge = subparsers.add_parser('merge', help='Merge current system state into the original one')
    parser_merge.set_defaults(func=merge_state)

    command_line = parser.parse_args(sys.argv[1:])

'''
Enable freeze mode
'''
def enable_freeze():
    status = get_status()
    if status == 'enabled':
        print "ROSA Freeze is already enabled."
        exit(0)
    elif status == 'disabled_pending':
        print "You didn't reboot computer after ROSA Freeze was disabled. Please reboot the machine first."
        exit(0)

    # For safety - load aufs module
    os.system("modprobe aufs")

    # Enable aufs mounting for root in dracut
    enable_freeze_dracut()

    # Make sure that aufs kernel module is loaded at boot time
    if not os.path.isfile('/etc/modules-load.d/aufs.conf'):
        aufs_conf = open('/etc/modules-load.d/aufs.conf', 'w')
        aufs_conf.write('aufs\n')
        aufs_conf.close()

    # Finally, let's enter freeze mode right now
    enable_freeze_now()

'''
Modify dracut parameters in grub config - add aufs_root
'''
def enable_freeze_dracut():
    grub_cfg = open(grub_cfg_name, 'r')
    grub_new_cfg = open(grub_tmp_cfg_name, 'w')

    for line in grub_cfg:
        if line.startswith('GRUB_CMDLINE_LINUX_DEFAULT'):
            line = re.sub(r'([\'"]\s*)$', r' aufs_root=UUID=\1', line)
        grub_new_cfg.write(line)
    grub_new_cfg.close()

    shutil.move(grub_tmp_cfg_name, grub_cfg_name)
    os.system("update-grub2")

'''
Enter freeze mode in a running system
'''
def enable_freeze_now():
    os.system("mkdir -p /tmp/xinos")
    os.system("mkdir -p /tmp/sysroot-rw")
    os.system("mkdir -p /tmp/sysroot-ro")
    os.system("mount -o bind / /tmp/sysroot-ro")
    os.system("mount -n -t tmpfs tmpfs /tmp/sysroot-rw")

    for d in os.listdir('/'):
        if os.path.isdir("/" + d) and d not in skip_dirs:
            if d == "root":
                os.system("mkdir -m 750 -p /tmp/sysroot-rw/" + d)
            else:
                os.system("mkdir -m 755 -p /tmp/sysroot-rw/" + d)
            os.system("mount -n -t aufs -o nowarn_perm,noatime,xino=/tmp/xinos/" + d + ".xino.aufs,dirs=/tmp/sysroot-rw/" + d + "=rw:/" + d + "=rr none /" + d)

'''
Disable freeze mode
'''
def disable_freeze():
    status = get_status()
    if status != 'enabled':
        print "You don't have ROSA Freeze enabled."
        exit(0)

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
    disable_freeze_dracut('/tmp/sysroot-orig/')
    os.system("chroot /tmp/sysroot-orig/ update-grub2")

    # Cleanup
    os.system("umount /tmp/sysroot-orig")
    os.system("rm -f /tmp/sysroot-orig")

'''
Modify dracut parameters in grub config - drop aufs_root
'''
def disable_freeze_dracut(chroot=""):
    grub_cfg = open(chroot + grub_cfg_name, 'r')
    grub_new_cfg = open(chroot + grub_tmp_cfg_name, 'w')

    for line in grub_cfg:
        if line.startswith('GRUB_CMDLINE_LINUX_DEFAULT'):
            line = re.sub(' aufs_root=UUID=', '', line)
        grub_new_cfg.write(line)
    grub_new_cfg.close()

    shutil.move(chroot + grub_tmp_cfg_name, chroot + grub_cfg_name)


'''
Try to merge current state to the original filesystem
'''
def merge_state():
    status = get_status()
    if status != 'enabled':
        print "You don't have ROSA Freeze enabled."
        exit(0)

    # Find original root device and mount it to /tmp/sysroot-orig
    orig_root = os.popen("findmnt -n -o SOURCE --target /tmp/sysroot-ro").read().rstrip()
    os.system("mkdir -p /tmp/sysroot-orig")
    os.system("mount -o rw " + orig_root + " /tmp/sysroot-orig")

    # rsync current state of 'frozen' folders.
    # Note that we don't support situation when some of these folders
    # are originally mounted from different partitions
    # (e.g., /var on separate partinion is not supported)
    for d in os.listdir('/'):
        if os.path.isdir("/" + d) and d not in skip_dirs:
            os.system("rsync -avH --delete /" + d + " /tmp/sysroot-orig")

    # Cleanup
    os.system("umount /tmp/sysroot-orig")
    os.system("rm -f /tmp/sysroot-orig")

'''
Get current status of ROSA Freeze
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


'''
Print current status of ROSA Freeze to console
'''
def print_status():
    status = get_status()
    if status == 'enabled':
        print "Freeze mode is enabled"
    elif status == 'disabled':
        print "Freeze mode is disabled"
    elif status == 'disabled_pending':
        "Freeze mode has been disabled, but you need to reboot to work with unfrozen system"
    else:
        print "INTERNAL ERROR, get_status() returned " + status


if __name__ == '__main__':
    parse_command_line()
    command_line.func()
