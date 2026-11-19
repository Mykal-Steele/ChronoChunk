"""The first-boot file sets the VM up the way the live one runs."""
import re


def _cloud_init() -> str:
    with open("deployment/cloud-init.yml") as f:
        return f.read()


def test_the_vm_keeps_the_bot_in_ram_instead_of_swapping_it_out():
    # With the default swappiness of 60 the kernel moved about 40 MB of the idle bot to the
    # swap file while 500 MB of RAM was free. The next slash command had to read it back from
    # disk, took over Discord's 3 seconds, and /help failed with "Unknown interaction" (2026-11-19)
    text = _cloud_init()
    assert re.search(r"path: /etc/sysctl\.d/90-chronochunk\.conf\n\s+content: \|\n\s+vm\.swappiness = 10", text)
    assert re.search(r"runcmd:(\n\s+- .*)*\n\s+- sysctl --system", text)


def test_the_vm_still_has_swap_for_image_builds():
    assert re.search(r"swap:\n\s+filename: /swapfile\n\s+size: 2G", _cloud_init())
