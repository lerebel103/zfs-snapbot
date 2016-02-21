#!/usr/bin/env python

""" Creates and maintains ZFS snapshots
"""

import os
import subprocess
import argparse
import ConfigParser
import sys
from syslog import syslog
from datetime import datetime, timedelta

DATE_FORMAT = '%Y-%m-%d_%H:%M'

SNAP_PREFIX = 'snap-'
DAILY_PREFIX = 'daily-'
WEEKLY_PREFIX = 'weekly-'
MONTHLY_PREFIX = 'monthly-'

DEFAULT_interval        = 15
DEFAULT_max_intervals   = 24
DEFAULT_max_days        = 7
DEFAULT_max_weeks       = 4
DEFAULT_max_months      = 12


def main():
    parser = argparse.ArgumentParser(
        description='Creates and maintains ZFS snapshots.',
        fromfile_prefix_chars='@')

    parser.add_argument(
        '-c',
        '--config',
        default='/etc/zfs-snapbot.conf',
        help='Auto snapshot config file.')

    args = parser.parse_args()
    config = ConfigParser.SafeConfigParser(allow_no_value=True)
    config.read([os.path.expanduser(args.config)])

    # Check when last snapshot was made, create new one if interval exceeded
    now = datetime.now()
    for section in config.sections():
        do_section(section.strip(), config, now)

def _get_config_value(config, section, key, default):
    if config.has_option(section, key):
        return config.get(section, key)
    else:
        return default

def _get_config_value_int(config, section, key, default):
    return int(_get_config_value(config, section, key, default))

def do_section(section, config, now):
    interval = _get_config_value_int(config, section, 'snap_interval', DEFAULT_interval)
    if now.minute % interval == 0:

        # Create new snapshots
        suffix = now.strftime(DATE_FORMAT)
        (new_created, snapshots) = snapshot(config, section, suffix, now)

        # Great now trim
        if new_created:
            _trim_all_snapshots(config, section, snapshots)

def snapshot(config, section, suffix, now):
    # List existing snapshots
    snapshots = list()
    cmd = 'zfs list -r -t snapshot {}'.format(section)
    (stdoutdata, stderrdata) = exec_cmd(cmd)
    for line in stdoutdata.splitlines(False)[1:]:
        snapshots.append(line.split()[0])

    # Regular snap
    new_created = _create_snapsthot(section, SNAP_PREFIX, suffix, snapshots)

    interval = _get_config_value_int(config, section, 'snap_interval', DEFAULT_interval)
    next_interval = now + timedelta(minutes=interval)

    # Are we on a day boundary?
    if next_interval.weekday() != now.weekday():
        _create_snapsthot(section, DAILY_PREFIX, suffix, snapshots)
        # Are we on a week boundary?
        if now.weekday() == 0:
            _create_snapsthot(section, WEEKLY_PREFIX, suffix, snapshots)
        # Are we on a month boundary?
        if now.day == 1:
            _create_snapsthot(section, MONTHLY_PREFIX, suffix, snapshots)

    snapshots.sort(reverse=True)
    return (new_created, snapshots)

def _create_snapsthot(path, prefix, suffix, snapshots):

    snapshot_name = '{}@{}{}'.format(path, prefix, suffix)
    new_created = False
    if snapshot_name not in snapshots:

        # Here's our new snapshot
        cmd = 'zfs snapshot -r {}'.format(snapshot_name)
        exec_cmd(cmd)
        snapshots.append(snapshot_name)
        print 'Created snapshot ' + snapshot_name

        new_created = True
    else:
        msg = 'Snapshot {} already exists, skipping.'.format(snapshot_name)
        print(msg)

    return (new_created, snapshots)

def _trim_all_snapshots(config, section, snapshots):
        # SNAPS
        count = _get_config_value_int(
            config, section, 'max_snaps', DEFAULT_max_intervals)
        _trim_snapshots(snapshots, count, SNAP_PREFIX)
        #
        # Days
        count = _get_config_value_int(
            config, section, 'max_days', DEFAULT_max_days)
        _trim_snapshots(snapshots, count, DAILY_PREFIX)
        #
        # Week
        count = _get_config_value_int(
            config, section, 'max_weeks', DEFAULT_max_weeks)
        _trim_snapshots(snapshots, count, WEEKLY_PREFIX)
        #
        # Month
        count = _get_config_value_int(
            config, section, 'max_months', DEFAULT_max_months)
        _trim_snapshots(snapshots, count, MONTHLY_PREFIX)

def _trim_snapshots(snapshots, max_count, prefix):
    old_snaps = list(s for s in snapshots if prefix in s)
    old_snaps = old_snaps[max_count:]
    for snap in old_snaps:
        exec_cmd('zfs destroy {}'.format(snap))


def exec_cmd(cmd):
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
    )
    (stdoutdata, stderrdata) = p.communicate()
    if  p.returncode != 0:
        msg = stderrdata
        err = 'ZFS list snapshot error:\n{}'.format(msg)
        print err
        syslog(err)
        raise subprocess.CalledProcessError(p.returncode, cmd, msg)

    return (stdoutdata, stderrdata)

if __name__ == "__main__":
    main()
