import csv
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from datetime import datetime, timezone
from numpy import ndarray
from matplotlib.axes import Axes
from pathlib import Path

######################################################
# This file has been taken and amended from HypeMan! #
######################################################


def read_trapsheet(filename: str) -> dict[str, ndarray]:
    # read a trap sheet into a dictionary as numpy arrays
    d = {}
    with open(filename, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for k in reader.fieldnames:
            d[k] = np.array([])

        for row in reader:
            for k in reader.fieldnames:
                svalue = row[k]
                try:
                    fvalue = float(svalue)
                    d[k] = np.append(d[k], fvalue)
                except ValueError:
                    d[k] = svalue
    return d


def set_spine(ax, color):
    ax.spines['bottom'].set_color(color)
    ax.spines['top'].set_color(color)
    ax.spines['left'].set_color(color)
    ax.spines['right'].set_color(color)


def add_aoa_rect(ax, v1, v2, color, alpha):
    rect = patches.Rectangle((0, v1), 1.2, v2 - v1, linewidth=0, edgecolor='none', facecolor=color, alpha=alpha)
    ax.add_patch(rect)


def plot_trapsheet(axs: list[Axes], ts: dict[str, ndarray], pinfo: dict[str, str], trapfile: str):
    theta_brc = -9.0
    if pinfo['aircraft'] == 'AV-8B':
        theta_brc = 0.0

    facecolor = '#404040'
    referencecolor = '#A6A6A6'  # glide slope and flight path references
    gridcolor = '#585858'
    spinecolor = gridcolor
    labelcolor = '#BFBFBF'

    dx = 20  # -151;
    dy = 20  # -6;

    num_aoa = 3
    feet = -6076.12

    theta = theta_brc * np.pi / 180.0

    rotMatrix = np.array([[np.cos(theta), -np.sin(theta)],
                          [np.sin(theta), np.cos(theta)]])

    xy = np.array([ts['X'] + dx, ts['Z'] + dy]) / 1852.0
    xy = np.dot(rotMatrix, xy)

    # ========================================================================
    # %% Lineup
    ax = axs[1]
    ax.set_ylim([-401.0, 801.0])
    ax.set_facecolor(facecolor)
    ax.plot(xy[0], feet * xy[1], 'g', linewidth=16, alpha=0.01)

    if pinfo['aircraft'] == 'AV-8B':
        m = np.array(ax.get_xlim())
        m[0] = 0
        ax.plot(m, [50, 50], referencecolor, linewidth=2, alpha=0.8)
    else:
        m = np.array(ax.get_xlim())
        m[0] = 0
        ax.plot(m, [0, 0], referencecolor, linewidth=2, alpha=0.8)

    ax.plot(xy[0], feet * xy[1], 'g', linewidth=16, alpha=0.1)
    ax.plot(xy[0], feet * xy[1], 'g', linewidth=10, alpha=0.1)
    ax.plot(xy[0], feet * xy[1], 'g', linewidth=6, alpha=0.15)
    ax.plot(xy[0], feet * xy[1], 'w-', linewidth=1, alpha=0.45)

    ax.grid(linestyle='-', linewidth='0.5', color=gridcolor)
    ax.tick_params(axis=u'both', which=u'both', length=0)
    set_spine(ax, 'none')
    ax.spines['right'].set_color(spinecolor)
    ax.spines['left'].set_color(spinecolor)
    plt.setp(ax.get_xticklabels(), color=labelcolor)
    plt.setp(ax.get_yticklabels(), color=labelcolor)

    xpoint = 0.195
    xpointsize = 9
    mystr = 'Lineup'
    ax.text(xpoint, 600, mystr, color=labelcolor, fontsize=xpointsize, alpha=0.5)

    p = Path(trapfile)
    ps = p.stem

    sh = 'SH_'
    perfect_pass = "unicorn_"
    if sh in ps:
        if perfect_pass in ps:
            ax.text(0.5, 0.75, ' *** SIERRA-HOTEL UNICORN PASS !! ***',
                    verticalalignment='bottom', horizontalalignment='center',
                    transform=ax.transAxes,
                    color='darkblue', fontsize=22)
            ax.text(0.505, 0.755, ' *** SIERRA-HOTEL UNICORN PASS !! ***',
                    verticalalignment='bottom', horizontalalignment='center',
                    transform=ax.transAxes,
                    color='lightblue', fontsize=22)
        else:
            ax.text(0.5, 0.75, 'SIERRA-HOTEL PASS',
                    verticalalignment='bottom', horizontalalignment='center',
                    transform=ax.transAxes,
                    color='yellow', fontsize=15)
    ax.invert_xaxis()

    # %% GLIDE SLOPE
    ax = axs[0]
    ax.set_ylim([-1, 650])  # Glideslope Reference scale from 0 to 650 feet
    ax.set_facecolor(facecolor)

    if pinfo['aircraft'] == 'AV-8B':
        xgs = xy[0]
        zt = 6076.12 * xgs * np.tan(3.5 * np.pi / 180.0)
        gx = 0
        gz = 40
        ax.plot(xy[0], ts['Alt'], 'g', linewidth=16, alpha=0.0)
        ax.plot(xgs + gx, zt + gz + 40, referencecolor, linewidth=1.1, alpha=1)
    else:
        xgs = xy[0]
        zt = 6076.12 * xgs * np.tan(3.5 * np.pi / 180.0)
        gx = 0
        gz = 40
        ax.plot(xy[0], ts['Alt'], 'g', linewidth=16, alpha=0.0)
        ax.plot(xgs + gx, zt + gz, referencecolor, linewidth=1.1, alpha=1)

    # "glow" effect arond the glideslope line
    ax.plot(xy[0], ts['Alt'] + 60, 'g', linewidth=8, alpha=0.1)
    ax.plot(xy[0], ts['Alt'] + 60, 'g', linewidth=5, alpha=0.1)
    ax.plot(xy[0], ts['Alt'] + 60, 'g', linewidth=3, alpha=0.15)
    ax.plot(xy[0], ts['Alt'] + 60, 'w-', linewidth=1, alpha=0.45)

    ax.grid(linestyle='-', linewidth='0.5', color=gridcolor)
    ax.tick_params(axis=u'both', which=u'both', length=0)
    set_spine(ax, 'none')
    ax.spines['right'].set_color(spinecolor)
    ax.spines['left'].set_color(spinecolor)

    if pinfo['aircraft'] == 'AV-8B':
        # top down view
        carrier01 = plt.imread('./plugins/greenieboard/img/boat03_2.png')
        ax.figure.figimage(carrier01, 910, 340, alpha=.75, zorder=1, clip_on=True)
        # side view for the glideslope plot
        carrier02 = plt.imread('./plugins/greenieboard/img/boat05_2.png')
        ax.figure.figimage(carrier02, 910, 560, alpha=0.75, zorder=1, clip_on=True)
    else:
        carrier01 = plt.imread('./plugins/greenieboard/img/boat03.png')
        ax.figure.figimage(carrier01, 930, 343, alpha=.45, zorder=1, clip_on=True)
        carrier02 = plt.imread('./plugins/greenieboard/img/boat05.png')
        ax.figure.figimage(carrier02, 930, 565, alpha=.45, zorder=1, clip_on=True)

    plt.setp(ax.get_xticklabels(), color=labelcolor)
    plt.setp(ax.get_yticklabels(), color=labelcolor)

    ax.text(xpoint, 500, 'Glide Slope', color=labelcolor, fontsize=xpointsize, alpha=0.5)
    ax.invert_xaxis()

    # %% Angle of Attack
    ax = axs[2]

    ax.xaxis.label.set_color(labelcolor)
    plt.setp(ax.get_xticklabels(), color=labelcolor)
    plt.setp(ax.get_yticklabels(), color=labelcolor)
    ax.set_xlabel("Distance (Nautical Miles)")

    maxvalue = np.max(ts['AoA'][:-num_aoa])
    minvalue = np.min(ts['AoA'][:-num_aoa])

    hornet_aoa = 'FA-18C_hornet'
    hawk_aoa = 'T-45'
    tomcatA_aoa = 'F-14A-135-GR'
    tomcatB_aoa = 'F-14B'
    harrier_aoa = 'AV8BNA'
    skyhawk_aoa = 'A-4E-C'

    if hornet_aoa in ps:  # 7.4 on speed min, 8.1 on speed, 8.8 onspeed max
        if maxvalue < 10 and minvalue > 6:
            maxvalue = 10.01
            minvalue = 5.99

        if maxvalue > 10 and minvalue > 6:
            minvalue = 3.99
        # if minvalue > 5.01:
        #    minvalue = 5.01

        ax.set_ylim([minvalue, maxvalue])
        ax.set_facecolor(facecolor)

        ax.text(xpoint, 9.5, 'AoA', color=labelcolor, fontsize=xpointsize, alpha=0.5)

        lm = ax.get_xlim()
        xmax = lm[1]

        if xmax < 0.7:
            xmax = 0.7

        if xmax > 1.2:
            xmax = 1.2

        ax.plot([0, xmax], [8.8, 8.8], linewidth=1.2, alpha=0.8, linestyle='--')
        ax.plot([0, xmax], [7.4, 7.4], linewidth=1.2, alpha=0.8, linestyle='--')

    elif hawk_aoa in ps:  # 6.75 onspeed min, 7.00 onspeed, 7.25 onspeed max
        if maxvalue < 8.5 and minvalue > 6:
            maxvalue = 8.51
            minvalue = 6.01

        if maxvalue > 10 and minvalue > 4:
            minvalue = 3.99

        ax.set_ylim([minvalue, maxvalue])
        ax.set_facecolor(facecolor)

        ax.text(xpoint, 9.5, 'AoA', color=labelcolor, fontsize=xpointsize, alpha=0.5)

        lm = ax.get_xlim()
        xmax = lm[1]

        xmax = max(0.8, xmax)
        xmax = min(1.2, xmax)

        ax.plot([0, xmax], [7.25, 7.25], linewidth=1.2, alpha=0.8, linestyle='--')
        ax.plot([0, xmax], [6.75, 6.75], linewidth=1.2, alpha=0.8, linestyle='--')

    elif tomcatA_aoa in ps:
        if maxvalue < 12.0 and minvalue > 8.0:
            maxvalue = 12.01
            minvalue = 7.99

        if maxvalue > 12.0 and minvalue > 8.0:
            minvalue = 5.99

        ax.set_ylim([minvalue, maxvalue])
        ax.set_facecolor(facecolor)
        ax.text(xpoint, 9.5, 'AoA', color=labelcolor, fontsize=xpointsize, alpha=0.5)

        lm = ax.get_xlim()
        xmax = lm[1]
        xmax = max(0.7, xmax)
        xmax = min(1.2, xmax)
        ax.plot([0, xmax], [10.818, 10.818], linewidth=1.2, alpha=0.8, linestyle='--')
        ax.plot([0, xmax], [9.9, 9.9], linewidth=1.2, alpha=0.8, linestyle='--')

    elif tomcatB_aoa in ps:

        if maxvalue < 12.0 and minvalue > 8.0:
            maxvalue = 12.01
            minvalue = 7.99

        if maxvalue > 12.0 and minvalue > 8.0:
            minvalue = 5.99

        ax.set_ylim([minvalue, maxvalue])
        ax.set_facecolor(facecolor)

        ax.text(xpoint, 9.5, 'AoA', color=labelcolor, fontsize=xpointsize, alpha=0.5)

        lm = ax.get_xlim()
        xmax = lm[1]
        xmax = max(0.7, xmax)
        xmax = min(1.2, xmax)

        ax.plot([0, xmax], [10.818, 10.818], linewidth=1.2, alpha=0.8, linestyle='--')
        ax.plot([0, xmax], [9.9, 9.9], linewidth=1.2, alpha=0.8, linestyle='--')

    elif harrier_aoa in ps:
        if maxvalue < 13 and minvalue > 9:
            maxvalue = 13.01
            minvalue = 8.99

        if maxvalue > 13 and minvalue > 9:
            minvalue = 6.99

        if maxvalue > 14 and minvalue < 8:
            maxvalue = 15.0
            minvalue = 7.0

        if minvalue > 5.01:
            minvalue = 5.01

        ax.set_ylim([minvalue, maxvalue])
        ax.set_facecolor(facecolor)

        ax.text(xpoint, 9.5, 'AoA', color=labelcolor, fontsize=xpointsize, alpha=0.5)

        lm = ax.get_xlim()
        xmax = lm[1]
        xmax = max(0.7, xmax)
        xmax = min(1.2, xmax)

        ax.plot([0, xmax], [12.0, 12.0], linewidth=1.2, alpha=0.8, linestyle='--')
        ax.plot([0, xmax], [10.0, 10.0], linewidth=1.2, alpha=0.8, linestyle='--')

    elif skyhawk_aoa in ps:
        if maxvalue < 10.75 and minvalue > 6.75:
            maxvalue = 10.76
            minvalue = 6.74

        if maxvalue > 10.75 and minvalue > 6.75:
            minvalue = 4.74

        ax.set_ylim([minvalue, maxvalue])
        ax.set_facecolor(facecolor)

        ax.text(xpoint, 9.5, 'AoA', color=labelcolor, fontsize=xpointsize, alpha=0.5)

        lm = ax.get_xlim()
        xmax = lm[1]
        xmax = max(0.7, xmax)
        xmax = min(1.2, xmax)

        ax.plot([0, xmax], [9.0, 9.0], linewidth=1.2, alpha=0.8, linestyle='--')
        ax.plot([0, xmax], [8.5, 8.5], linewidth=1.2, alpha=0.8, linestyle='--')

    ax.plot(xy[0][:-num_aoa], ts['AoA'][:-num_aoa], 'g-', linewidth=8, alpha=0.1)
    ax.plot(xy[0][:-num_aoa], ts['AoA'][:-num_aoa], 'g-', linewidth=5, alpha=0.1)
    ax.plot(xy[0][:-num_aoa], ts['AoA'][:-num_aoa], 'g-', linewidth=3, alpha=0.15)
    ax.plot(xy[0][:-num_aoa], ts['AoA'][:-num_aoa], 'w-', linewidth=1, alpha=0.45)
    ax.grid(linestyle='-', linewidth='0.5', color=gridcolor)
    ax.tick_params(axis=u'both', which=u'both', length=0)
    set_spine(ax, 'none')
    ax.spines['right'].set_color(spinecolor)
    ax.spines['left'].set_color(spinecolor)
    ax.spines['bottom'].set_color(spinecolor)

    ax.set_xlim([0.001, xmax])
    ax.invert_xaxis()


def parse_filename(vinput) -> dict[str, str]:
    pinfo = {}
    p = Path(vinput)
    last_modified = p.stat().st_mtime
    mod_timestamp = datetime.fromtimestamp(last_modified)

    timestampStr = mod_timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    pinfo['time'] = timestampStr
    ps = p.stem

    ps = ps.replace('AIRBOSS-', '')
    ind = ps.find('-')
    ps = ps[ind + 1:-1]
    ind = ps.rfind('-')
    ps = ps[0:ind]

    hornet = 'FA-18C_hornet'
    tomcatB = 'F-14B'
    harrier = 'AV8BNA'
    tomcatA = 'F-14A-135-GR'
    scooter = 'A-4E-C'
    goshawk = 'T-45'

    if hornet in ps:
        ps = ps.replace(hornet, '')
        pinfo['aircraft'] = 'F/A-18C'
    elif goshawk in ps:
        ps = ps.replace(goshawk, '')
        pinfo['aircraft'] = 'T-45C'
    elif tomcatA in ps:
        ps = ps.replace(tomcatA, '')
        pinfo['aircraft'] = 'F-14A-135-GR'
    elif tomcatB in ps:
        ps = ps.replace(tomcatB, '')
        pinfo['aircraft'] = 'F-14B'
    elif harrier in ps:
        ps = ps.replace(harrier, '')
        pinfo['aircraft'] = 'AV-8B'
    elif scooter in ps:
        ps = ps.replace(scooter, '')
        pinfo['aircraft'] = 'A-4'
    else:
        print('unknown aircraft.')
    pinfo['callsign'] = ps[0:-1]
    return pinfo
