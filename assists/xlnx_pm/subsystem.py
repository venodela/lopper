# /*
# * Copyright (c) 2019,2020,2021 Xilinx Inc. All rights reserved.
# *
# * Author:
# *     Ben Levinsky <ben.levinsky@xilinx.com>
# *     Izhar Ameer Shaikh <izhar.ameer.shaikh@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

from lopper import Lopper
import lopper
from lopper_tree import *
from xlnx_versal_power import *
import functools

sys.path.append(os.path.dirname(__file__))

import protections
import xppu


class Device():
    def __init__(self, nodes, node_id, flags, firewall={}):
        self.nodes = nodes
        self.node_id = node_id
        self.flags = flags
        self.firewall = firewall
        self.pm_reqs = [0, 0, 0, 0]


class Subsystem():
    def __init__(self, sub_node):
        self.flag_references = {}
        self.dev_dict = {}
        self.sub_node = sub_node


def get_sub_id_and_str(subsys_node):
    sub_id = subsys_node.propval("id")
    if isinstance(sub_id, list):
        sub_id = sub_id[0]
    s_str = "subsystem_" + str(sub_id)
    s_id = 0x1c000000 | sub_id
    return s_str, s_id


def valid_subsystems(domain_node, sdt, options):
    # return list of valid Xilinx subsystems
    subsystems = []

    # a node is a Xilinx subsystem if:
    # one of the top level domain nodes
    # has compatible string: "xilinx,subsystem-v1"
    # has id property
    for node in domain_node.subnodes():
        if node.parent != domain_node:
            continue
        if node.propval("id") == ['']:
            continue

        if node.propval("compatible") == ['']:
            continue

        compat = node.propval("compatible")
        for c in compat:
            if c == 'xilinx,subsystem-v1':
                subsystems.append(Subsystem(node))

    return subsystems


def usage_no_restrictions(node):
    firewallconf_default = node.propval("firewallconf-default")
    return (firewallconf_default != [''] and firewallconf_default[0] == 0
            and firewallconf_default[1] == 0)


def find_cpu_ids(node, sdt):
    # given a node that has a cpus property, return list of  corresponding
    # XilPM Node IDs for the core node
    cpu_phandle = node.propval('cpus')[0]
    cpu_mask = node.propval('cpus')[1]
    dev_str = "dev_"
    cpu_xilpm_ids = []

    cpu_node = sdt.tree.pnode(cpu_phandle)
    if cpu_node is None:
        return cpu_xilpm_ids

    # based on cpu arg cpumask we can determine which cores are used
    if 'a72' in cpu_node.name:
        dev_str += "acpu"
        cpu_xilpm_ids.append(existing_devices["dev_ams_root"])
        cpu_xilpm_ids.append(existing_devices["dev_l2_bank_0"])
        cpu_xilpm_ids.append(existing_devices["dev_aie"])

    elif 'r5' in cpu_node.name:
        dev_str += "rpu0"
    else:
        # for now only a72 or r5 are cores described in Xilinx subsystems
        return cpu_xilpm_ids

    if cpu_mask & 0x1:
        cpu_xilpm_ids.append(existing_devices[dev_str + "_0"])
    if cpu_mask & 0x2:
        cpu_xilpm_ids.append(existing_devices[dev_str + "_1"])

    return cpu_xilpm_ids


def prelim_flag_processing(node, prefix):
    # return preliminary processing of flags
    flags = ''
    if node.propval(prefix + '-flags-names') == ['']:
        flags = 'default'
    else:
        flags = node.propval(prefix + '-flags-names')
        if isinstance(flags, list):
            flags = flags[0]

    if usage_no_restrictions(node):
        flags = flags + '::no-restrictions'

    return flags


def find_mem_ids(node, sdt, fw_config={}):
    # given a node that has a memory or sram property, return list of  corresponding
    # XilPM Node IDs for the mem/sram node
    flags = prelim_flag_processing(node, 'memory')

    mem_xilpm_ids = []
    if node.propval('memory') != ['']:
        xilpm_id = mem_xilpm_ids.append(
            (existing_devices['dev_ddr_0'], flags, fw_config))

    return mem_xilpm_ids


def find_sram_ids(node, sdt, fw_config={}):
    sram_base = node.propval('sram')[0]
    sram_end = node.propval('sram')[1] + sram_base
    mem_xilpm_ids = []
    id_with_flags = []
    flags = prelim_flag_processing(node, 'sram')

    ocm_len = 0xFFFFF
    if 0xFFFC0000 <= sram_base <= 0xFFFFFFFF:
        # OCM
        if sram_base <= 0xFFFC0000 + ocm_len:
            mem_xilpm_ids.append("dev_ocm_bank_0")
        if sram_base < 0xFFFDFFFF and sram_end > 0xFFFD0000:
            mem_xilpm_ids.append("dev_ocm_bank_1")
        if sram_base < 0xFFFEFFFF and sram_end > 0xFFFE0000:
            mem_xilpm_ids.append("dev_ocm_bank_2")
        if sram_base < 0xFFFFFFFF and sram_end > 0xFFFF0000:
            mem_xilpm_ids.append("dev_ocm_bank_3")
    elif 0xFFE00000 <= sram_base <= 0xFFEBFFFF:
        # TCM
        if sram_base < 0xFFE1FFFF:
            mem_xilpm_ids.append("dev_tcm_0_a")
        if sram_base <= 0xFFE2FFFF and sram_end > 0xFFE20000:
            mem_xilpm_ids.append("dev_tcm_0_b")
        if sram_base <= 0xFFE9FFFF and sram_end > 0xFFE90000:
            mem_xilpm_ids.append("dev_tcm_1_a")
        if sram_base <= 0xFFEBFFFF and sram_end > 0xFFEB0000:
            mem_xilpm_ids.append("dev_tcm_1_b")

    for i in mem_xilpm_ids:
        id_with_flags.append((existing_devices[i], flags, fw_config))

    return id_with_flags


def xilpm_id_from_devnode(subnode):
    power_domains = subnode.propval('power-domains')
    if power_domains != [''] \
            and power_domains[1] in xilinx_versal_device_names.keys():
        return power_domains[1]
    elif subnode.name in misc_devices:
        if misc_devices[subnode.name] is not None:
            mailbox_xilpm_id = existing_devices[misc_devices[subnode.name]]
            if mailbox_xilpm_id is not None:
                return mailbox_xilpm_id
    return -1


def process_acccess(subnode, sdt, options, fw_config={}):
    # return list of node ids and corresponding flags
    access_list = []
    access_flag_names = subnode.propval('access-flags-names')
    access_phandles = subnode.propval('access')
    f = ''
    if usage_no_restrictions(subnode):
        f += '::no-restrictions'

    if len(access_flag_names) != len(access_phandles):
        print("WARNING: subnode: ", subnode,
              " has length of access and access-flags-names mismatch: ",
              access_phandles, access_flag_names)
        return access_list

    for index, phandle in enumerate(access_phandles):
        dev_node = sdt.tree.pnode(phandle)
        if dev_node is None:
            print(
                "WARNING: acccess list device phandle does not have matching device in tree: ",
                hex(phandle), " for node: ", subnode)
            return access_list

        xilpm_id = xilpm_id_from_devnode(dev_node)
        if xilpm_id == -1:
            print("WARNING: no xilpm ID for node: ", dev_node)
            continue

        access_list.append(
            (xilpm_id, access_flag_names[index] + f + '::access', fw_config))
    return access_list


def document_requirement(output, subsystem, device):
    subsystem_name = "subsystem_" + str(subsystem.sub_node.propval("id"))
    cdo_sub_str, cdo_sub_id = get_sub_id_and_str(subsystem.sub_node)
    flags_arg = device.pm_reqs[0]

    print("#", file=output)
    print("#", file=output)
    print("# subsystem:", file=output)
    print("#    name: " + subsystem_name, file=output)
    print("#    ID: " + hex(cdo_sub_id), file=output)
    print("#", file=output)
    print("# node:", file=output)
    print("#    name: " + xilinx_versal_device_names[device.node_id],
          file=output)
    print("#    ID: " + hex(device.node_id), file=output)
    print("#", file=output)
    arg_names = {
        0: "flags",
        1: "XPPU Aperture Permission Mask",
        2: "Prealloc capabilities",
        3: "Quality of Service"
    }
    for index, flag in enumerate(device.pm_reqs):
        print("# requirements: ",
              arg_names[index],
              ": " + hex(flag),
              file=output)

    print(usage(flags_arg), file=output)
    print(security(flags_arg), file=output)
    print(prealloc_policy(flags_arg), file=output)

    if ((flags_arg & prealloc_mask) >> prealloc_offset == PREALLOC.REQUIRED):
        # detail prealloc if enabled
        print(prealloc_detailed_policy(device.pm_reqs[3]), file=output)

    if mem_regn_node(device.node_id):
        print(read_policy(flags_arg), file=output)
        print(write_policy(flags_arg), file=output)
        print(nsregn_policy(flags_arg), file=output)
        print("#", file=output)


def devid_to_devname(devid):
    try:
        name = xilinx_versal_device_names[devid]
    except:
        name = ''
    return name


def get_dev_firewall_config(node,
                            sdt,
                            options,
                            included=False,
                            parent=None,
                            subsys_node=None):
    # list of tuples, e.g. [(domain1, 0, (domain0, 3)..]
    fw_dev = {}
    for key in ['block', 'allow']:
        fw_dev[key] = []

    # check for firewallconf/firewallconf-default links
    fw = node.propval('firewallconf')
    if fw != ['']:
        if len(fw) % 3 != 0:  # firewallconf = <<link> <b/a/bd> <p>, ...>
            print("[WARNING] Invalid format for firewallconf {} for node {}".
                  format(fw, node.name))
            return fw_dev

        # retrieve device firewall links
        for f in protections.chunks(fw, 3):
            target_node = sdt.tree.pnode(f[0]).name
            if f[1] in [0, 2]:  # block/block-desirable
                fw_dev['block'].append((target_node, f[2]))
            elif f[1] in [1]:  # allow
                fw_dev['allow'].append((target_node, f[2]))
            else:
                print(
                    "[WARNING] Unsupported attribute at firewallconf {} for node {}"
                    .format(fw, node.name))

    fw_def = node.propval('firewallconf-default')
    if fw_def != ['']:
        if len(fw_def) != 2:  # firewallconf-default = <<b/a/bd> <p>>
            print(
                "[WARNING] Invalid format for firewallconf-default {} for node {}"
                .format(fw_def, node.name))
            return fw_dev

        # retrieve device firewall links
        for f in protections.chunks(fw_def, 2):
            if f[0] in [0, 2]:  # block/block-desirable
                fw_dev['block'].append(("rest", f[1]))
            elif f[0] in [1]:  # allow
                fw_dev['allow'].append(("rest", f[1]))
            else:
                print(
                    "[WARNING] Unsupported attribute at firewallconf-default {} for node {}"
                    .format(fw_def, node.name))

    # for non-resource-group nodes, allow bus-master-ids from the node
    # for resource group nodes, allow bus-master-ids from the parent where
    # it is included
    if parent is not None and subsys_node is not None:
        if parent.name == subsys_node.name:
            fw_dev['allow'].append((parent.name, 0))
        else:
            fw_dev['allow'].append((parent.name, 0))
            fw_dev['allow'].append((subsys_node.name, 0))
    elif parent is not None:
        fw_dev['allow'].append((parent.name, 0))
    elif subsys_node is not None:
        fw_dev['allow'].append((subsys_node.name, 0))

    return fw_dev


def get_aperture_mask(device):
    outmask = 0xfffff
    if device is None or device.firewall is None:
        return outmask

    default = [ action
               for action, masters in device.firewall.items()
               for m in masters
               if m[0] == "rest" ]

    if len(default) > 1:
        print("[WARNING] Conflicting default firewall configs for {}".format(
            devid_to_devname(device.node_id)))
        return outmask

    if default != []:
        default = default[0]

    if default == "allow":
        outmask = outmask & ~(0x3 << 18)  # Slot 19 and 20 is for ANY RO, RW
    elif default == "block" or default == []:
        outmask = 0x0

    # build up the aperture mask
    mask = {}
    for action, masters in device.firewall.items():
        mask[action] = 0
        for m in masters:
            if m[0] != "rest":
                mask[action] = \
                    mask[action] | protections.prot_map.derive_ss_mask(m[0])

    # set allowed masters and clear out blocked masters
    newmask = outmask | mask['allow'] & ~mask['block']

    return newmask


def is_domain_or_subsys(node):
    for compat in node.propval("compatible"):
        if compat in ["xilinx,subsystem-v1", "openamp,domain-v1"]:
            return True
    return False


def process_subsystem(subsystem,
                      subsystem_node,
                      sdt,
                      options,
                      included=False,
                      included_from=None):
    # given a device tree node
    # traverse for devices that are either implicitly
    # or explicitly lnked to the device tree node
    # this includes either Xilinx subsystem nodes or nodes that are
    # resource group included nodes

    # In addition, for each device, identify the flag reference that
    # corresponds.

    # collect nodes, xilpm IDs, flag strings here
    for node in subsystem_node.subnodes():
        device_flags = []
        parent_domain = None

        # skip flag references
        if 'flags' in node.abs_path:
            continue

        # retrieve firewallconf/firewallconf-def links for this node
        if is_domain_or_subsys(node) is True:
            # add to sub/dom -> bus master id map
            protections.setup_dom_to_bus_mids(node, sdt)
            fw_links = get_dev_firewall_config(node,
                                               sdt,
                                               options,
                                               included=False,
                                               parent=node,
                                               subsys_node=subsystem.sub_node)
        else:
            fw_links = get_dev_firewall_config(node,
                                               sdt,
                                               options,
                                               included=included,
                                               parent=included_from,
                                               subsys_node=subsystem.sub_node)

        if node.propval('cpus') != ['']:
            f = 'default'
            f += '::no-restrictions'  # by default, cores are not restricted
            for xilpm_id in find_cpu_ids(node, sdt):
                device_flags.append((xilpm_id, f, fw_links))

        if node.propval('memory') != ['']:
            device_flags.extend(find_mem_ids(node, sdt, fw_links))
        if node.propval('sram') != ['']:
            device_flags.extend(find_sram_ids(node, sdt, fw_links))
        if node.propval('access') != ['']:
            device_flags.extend(process_acccess(node, sdt, options, fw_links))

        if node.propval('include') != ['']:
            for included_phandle in node.propval('include'):
                included_node = sdt.tree.pnode(included_phandle)
                if included_node is None:
                    print("WARNING: included_phandle: ", hex(included_phandle),
                          " does not have corresponding node in tree.")
                else:
                    # recursive base case for handling resource group
                    process_subsystem(subsystem,
                                      included_node,
                                      sdt,
                                      options,
                                      included=True,
                                      included_from=node)

        # after collecting a device tree node's devices,
        # add this to subsystem's device nodes collection
        # if current node is resource group, recurse 1 time
        for xilpm_id, flags, firewall_config in device_flags:
            if included:
                if isinstance(flags, list):
                    flags = flags[0]
                flags = flags + "::included-from-" + subsystem_node.name

                # strip out access. as this is used for non-sharing purposes
                # if a device is in a resource group, it should not show as 'non-shared'
                flags.replace('access', '')

            # add to subsystem's list of xilpm nodes
            if xilpm_id not in subsystem.dev_dict.keys():
                subsystem.dev_dict[xilpm_id] = Device([node], xilpm_id,
                                                      [flags], firewall_config)
            else:
                device = subsystem.dev_dict[xilpm_id]
                device.nodes.append(node)
                device.flags.append(flags)


def construct_flag_references(subsystem):
    flags_list = subsystem.sub_node.propval("flags")
    for index, flags_name in enumerate(
            subsystem.sub_node.propval("flags-names")):
        ref_flags = [0x0, 0x0, 0x0, 0x0]

        current_base = index * 4  # 4 elements per requirement of device
        for i in range(0, 3):
            ref_flags[i] = flags_list[current_base + i]

        subsystem.flag_references[flags_name] = ref_flags


def determine_inclusion(device_flags, other_device_flags, sub, other_sub):

    # look for time share in the flags defined by each subsystem that correspond
    # to the device

    # first get current device
    dev_timeshare = device_flags[0].split("::")[0]
    if dev_timeshare not in sub.flag_references.keys():
        dev_timeshare = 'default'

    dev_timeshare = copy.deepcopy(sub.flag_references[dev_timeshare])

    # second get time share of other device
    other_dev_timeshare = device_flags[0].split("::")[0]
    if other_dev_timeshare not in other_sub.flag_references.keys():
        other_dev_timeshare = 'default'

    other_dev_timeshare = copy.deepcopy(
        other_sub.flag_references[other_dev_timeshare])
    included = 0x0
    for f in device_flags:
        if 'include' in f:
            included |= 0x1
        if 'access' in f and 'include' not in f:
            included |= 0x2
    for f in other_device_flags:
        if 'include' in f:
            included |= 0x8
        if 'access' in f and 'include' not in f:
            included |= 0x4

    # determine if timeshare present in one of the flags for a
    # device-subsystem link
    if dev_timeshare[0] & 0x3 == 0x3:
        included |= 16
    if other_dev_timeshare[0] & 0x3 == 0x3:
        included |= 32

    return included


def set_dev_pm_reqs(sub, device, usage):
    new_dev_flags = device.flags[0].split("::")[0]
    if new_dev_flags not in sub.flag_references.keys():
        new_dev_flags = 'default'
    new_dev_flags = copy.deepcopy(sub.flag_references[new_dev_flags])
    device.pm_reqs = new_dev_flags

    device.pm_reqs[0] |= usage
    device.pm_reqs[1] = get_aperture_mask(device)


def construct_pm_reqs(subsystems):
    # given list of all subsystems,
    # for each device
    #    look for device in other domains
    #        if found via include, update usage
    #        if found otherwise report as error
    #        else set as non shared
    #
    #    set rest of flags per relevant reference housed in subsystem
    for sub in subsystems:
        for device in sub.dev_dict.values():
            usage = 0x2  # non-shared
            included = 0x0
            for other_sub in subsystems:
                if sub == other_sub:
                    continue

                if device.node_id in other_sub.dev_dict.keys():
                    other_device = other_sub.dev_dict[device.node_id]
                    included = determine_inclusion(device.flags,
                                                   other_device.flags, sub,
                                                   other_sub)

                    # this means neither reference this via include so raise
                    # error
                    if included & 0x6 != 0x0:
                        print('WARNING: ', hex(device.node_id),
                              'found in multiple domains without includes ',
                              sub.sub_node, other_sub.sub_node, included,
                              usage, device.flags, other_device.flags)
                        return
                    if (included & 16 != 0) and (included & 32 == 0):
                        print(
                            'WARNING: ', hex(device.node_id),
                            'found in multiple domains with mismatch of timeshare',
                            sub.sub_node, other_sub.sub_node, included, usage)
                        return

                    if included == 0x1 or included == 0x8 or included == 0x9:
                        usage = 0x1  # update to shared
                else:
                    # if from resource group in only domain should still be
                    # shared
                    included = determine_inclusion(device.flags, [], sub,
                                                   other_sub)
                    if included == 0x1:
                        usage = 0x1

            for f in device.flags:
                if 'no-restrictions' in f:
                    usage = 0x0

            set_dev_pm_reqs(sub, device, usage)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_operations_allowed(subsystems, output):
    for sub in subsystems:
        host_sub_str, host_sub_id = get_sub_id_and_str(sub.sub_node)

        for other_sub in subsystems:
            if sub == other_sub:
                continue

            other_sub_id = other_sub.sub_node.propval("id")
            if isinstance(other_sub_id, list):
                other_sub_id = other_sub_id[0]

            other_sub_str = "subsystem_" + hex(other_sub_id)
            other_sub_id = 0x1c000000 | other_sub_id

            cdo_str = "# " + host_sub_str + " can  enact only non-secure ops upon " + other_sub_str
            cdo_cmd = "pm_add_requirement " + hex(host_sub_id) + " "
            cdo_cmd += hex(other_sub_id) + " " + hex(0x7)

            print(cdo_str, file=output)
            print(cdo_cmd, file=output)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_ggs_perms(sub, output):
    cdo_sub_str, cdo_sub_id = get_sub_id_and_str(sub.sub_node)

    for i in range(0x18248000, 0x18248003 + 1):
        dev_str = "ggs_" + hex(i & 0x7).replace('0x', '')
        dev_id = hex(i)

        cdo_str = "# " + cdo_sub_str + " can perform non-secure read/write "
        cdo_str += dev_str

        cdo_cmd = "pm_add_requirement " + hex(cdo_sub_id) + " "
        cdo_cmd += dev_id + " "
        cdo_cmd += hex(0x3)
        print(cdo_str, file=output)
        print(cdo_cmd, file=output)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_pggs_perms(sub, output):
    cdo_sub_str, cdo_sub_id = get_sub_id_and_str(sub.sub_node)

    for i in range(0x1824c004, 0x1824c007 + 1):
        dev_str = "pggs_" + hex((i & 0x7) - 0x4).replace('0x', '')
        dev_id = hex(i)

        cdo_str = "# " + cdo_sub_str + " can perform non-secure read/write "
        cdo_str += dev_str

        cdo_cmd = "pm_add_requirement " + hex(cdo_sub_id) + " "
        cdo_cmd += dev_id + " "
        cdo_cmd += hex(0x3)
        print(cdo_str, file=output)
        print(cdo_cmd, file=output)


# TODO hard coded for now. need to add this to spec, YAML, etc
def sub_reset_perms(sub, output):
    cdo_sub_str, cdo_sub_id = get_sub_id_and_str(sub.sub_node)
    print("#",
          cdo_sub_str,
          " can enact only non-secure system-reset (rst_pmc)",
          file=output)
    print("pm_add_requirement " + hex(cdo_sub_id) + " 0xc410002 0x1",
          file=output)


def sub_perms(subsystems, output):
    # add cdo commands for sub-sub permissions, reset perms and xGGS perms
    sub_operations_allowed(subsystems, output)

    for sub in subsystems:
        sub_ggs_perms(sub, output)
        sub_pggs_perms(sub, output)
        sub_reset_perms(sub, output)


def write_to_cdo(subsystems, outfile, verbose):
    # generate output cdo
    with open(outfile, "w") as output:
        print("# Lopper CDO export", file=output)
        print("version 2.0", file=output)
        print("marker", hex(0x64), '"Subsystem"', file=output)

        for sub in subsystems:
            # determine subsystem ID
            cdo_sub_str, cdo_sub_id = get_sub_id_and_str(sub.sub_node)
            # add subsystem
            print("# " + cdo_sub_str, file=output)
            print("pm_add_subsystem " + hex(cdo_sub_id), file=output)

        # add CDO commands for permissions
        sub_perms(subsystems, output)

        for sub in subsystems:
            # determine subsystem ID
            cdo_sub_str, cdo_sub_id = get_sub_id_and_str(sub.sub_node)
            # add reqs
            for device in sub.dev_dict.values():
                if device.node_id not in xilinx_versal_device_names.keys():
                    print("WARNING: ", hex(device.node_id),
                          ' not found in xilinx_versal_device_names')
                    return

                req_description = "# " + cdo_sub_str + ' ' + \
                    xilinx_versal_device_names[device.node_id]

                # form CDO flags in string for python
                req_str = 'pm_add_requirement ' + hex(cdo_sub_id) + ' ' + hex(
                    device.node_id)
                for req in device.pm_reqs:
                    req_str += ' ' + hex(req)

                # write CDO
                print(req_description, file=output)
                print(req_str, file=output)
        # Subsystem end
        print("marker", hex(0x65), '"Subsystem"', file=output)


def generate_cdo(root_node, domain_node, sdt, outfile, verbose, options):
    subsystems = valid_subsystems(domain_node, sdt, options)

    # prot:: setup protection nodes and mapping
    protections.setup_node_to_prot_map(root_node, sdt, options)

    for sub in subsystems:
        # collect device tree flags, nodes, xilpm IDs for each device linked to
        # a subsystem
        process_subsystem(sub, sub.sub_node, sdt, options)
        construct_flag_references(sub)

    # prot:: setup prot to bus mids map
    # protections.prot_map.print_ss_to_bus_mids_map()

    # generate xilpm reqs for each device
    construct_pm_reqs(subsystems)

    # write the output
    write_to_cdo(subsystems, outfile, verbose)

    # prot:: write protection output
    protections.write_cdo(outfile, verbose)