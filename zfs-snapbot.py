#!/usr/bin/env python

""" Creates and maintains ZFS snapshots
"""

import os
import subprocess
import argparse
import ConfigParser
import sys
import smtplib
import os.path
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from syslog import syslog
from datetime import datetime, timedelta

ZFS_COMMAND = '/usr/local/bin/zfs'
ZPOOL_COMMAND = '/usr/local/bin/zpool'

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
    bad_pools = dict()
    for section in config.sections():
        section = section.strip();
        if len(section):
            # Check pool first
            pool = section.split('/')[0]
            (stdoutdata, stderrdata) = exec_cmd(ZPOOL_COMMAND + ' status ' + pool)
            if 'ONLINE' in stdoutdata:
                do_section(section.strip(), config, now)
            else:
                # Uh something bad
                bad_pools[pool] = stdoutdata

    # Send alert if needed
    _alert(now, config, bad_pools)

def _can_send_alert(now, pools, check_file):
    """ Touch a file in temp filesystem, so we know alert has been sent.
    Clean up each day """
    can_send = len(pools) > 0

    if len(pools) == 0 and os.path.isfile(check_file):
        os.remove(check_file)
    else:
        pass
        #is_sent = False
        #with open(check_file, 'r') as f:
        #    for line in f:

    return can_send

def _alert(now, config, bad_pools):
    tmp_dir = tempfile.gettempdir()
    check_file = os.path.join(tmp_dir, 'zfs-snapbot.tmp')

    if not _can_send_alert(now, bad_pools.keys(), check_file):
        return

    # Ok, send alert then
    s = smtplib.SMTP_SSL(
        _get_config_value(config, ' ', 'smtp_host', None),
        _get_config_value_int(config, ' ', 'smtp_port', 587),
    )
    #s.ehlo()
    #s.starttls()
    #s.ehlo()
    s.login(
        user = _get_config_value(config, ' ', 'smtp_user', None),
        password = _get_config_value(config, ' ', 'smtp_password', None),
    )

    from_email = _get_config_value(config, ' ', 'smtp_from', None)
    to_email = _get_config_value(config, ' ', 'smtp_to', None)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = "[zfs-snapbot] ZPOOL Alert"
    msg['From'] = from_email
    msg['To'] = to_email

    html = """<html><body>
        <p>Hi there,</p>
        <p>I am afraid we have a problem:</p>"""
    for pool_state in bad_pools.values():
        html = '{}<pre>{}</pre>'.format(html, pool_state)
    html = html + """<p>Good luck with it!<br>
        -- zfs-snapbot.</p>
        </body></html>"""

    part2 = MIMEText(html, 'html')
    msg.attach(part2)
    s.sendmail(from_email, to_email, msg.as_string())
    s.quit()

    # Great mark check file
    with open(check_file, 'w') as f:
        for pool in bad_pools.keys():
            f.write(pool + '\n')

def _get_config_value(config, section, key, default):
    if config.has_option(section, key):
        return config.get(section, key)
    else:
        return default

def _get_config_value_int(config, section, key, default):
    return int(_get_config_value(config, section, key, default))

def do_section(section, config, now):
    # Create new snapshots
    suffix = now.strftime(DATE_FORMAT)
    (new_created, snapshots) = snapshot(config, section, suffix, now)

    # Great now trim
    if new_created:
        _trim_all_snapshots(config, section, snapshots)

def snapshot(config, section, suffix, now):
    # Desired snapshot interval
    interval = _get_config_value_int(
        config, section, 'snap_interval', DEFAULT_interval)

    # List existing snapshots
    snapshots = list()
    cmd = ZFS_COMMAND + ' list -r -t snapshot {}'.format(section)
    (stdoutdata, stderrdata) = exec_cmd(cmd)
    for line in stdoutdata.splitlines(False)[1:]:
        snapshots.append(line.split()[0])

    # Old snaps, retrieve last on record
    matching = [s for s in snapshots if SNAP_PREFIX in s]
    matching.sort(reverse=True)
    last_date = now - timedelta(minutes=interval+1)
    if matching:
        datestr = matching[0].replace('{}@{}'.format(section, SNAP_PREFIX), '')
        last_date = datetime.strptime(datestr, DATE_FORMAT)

    delta_minutes = (now - last_date).total_seconds() * 60
    if delta_minutes > interval:
        # Regular snap
        new_created = _create_snapsthot(section, SNAP_PREFIX, suffix, snapshots)

    # Are we on a day boundary?
    if last_date.weekday() != now.weekday():
        _create_snapsthot(section, DAILY_PREFIX, suffix, snapshots)
        # Are we on a week boundary?
        if now.weekday() == 0:
            _create_snapsthot(section, WEEKLY_PREFIX, suffix, snapshots)
        # Are we on a month boundary?
        if now.day == 1:
            _create_snapsthot(section, MONTHLY_PREFIX, suffix, snapshots)
    else:
        new_created = False

    snapshots.sort(reverse=True)
    return (new_created, snapshots)

def _create_snapsthot(path, prefix, suffix, snapshots):

    snapshot_name = '{}@{}{}'.format(path, prefix, suffix)
    new_created = False
    if snapshot_name not in snapshots:

        # Here's our new snapshot
        cmd = ZFS_COMMAND + ' snapshot -r {}'.format(snapshot_name)
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
        exec_cmd(ZFS_COMMAND + ' destroy {}'.format(snap))


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
        err = 'Command error:\n{}'.format(msg)
        print err
        syslog(err)
        raise subprocess.CalledProcessError(p.returncode, cmd, msg)

    return (stdoutdata, stderrdata)

if __name__ == "__main__":
    main()
