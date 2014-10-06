import os

os.system('dracut -f /boot/initrd-$(uname -r).img $(uname -r)')