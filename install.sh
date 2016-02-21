#!/bin/bash

cp zfs-snapbot.py /usr/local/bin/
cp com.willc.zfs-snapbot.plist /Library/LaunchDaemons/
launchctl unload com.willc.zfs-snapbot.plist 
launchctl load /Library/LaunchDaemons/com.willc.zfs-snapbot.plist 

