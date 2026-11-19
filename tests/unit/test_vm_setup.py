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
    assert re.search(r"runcmd:(\n\s+- .*)*\n\s+- sysctl -p /etc/sysctl\.d/90-chronochunk\.conf", text)


def test_the_vm_setup_never_reloads_every_sysctl_file():
    # "sysctl --system" also reloads Google's 60-gce-network-security.conf, which sets
    # net.ipv4.ip_forward=0. Docker turns forwarding on when it starts, and turning it off again
    # cut the container off from Discord: the bot was offline for 18 minutes on 2026-11-19
    assert "sysctl --system" not in _cloud_init()


def test_the_vm_still_has_swap_for_image_builds():
    assert re.search(r"swap:\n\s+filename: /swapfile\n\s+size: 2G", _cloud_init())
