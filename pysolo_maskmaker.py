#!/usr/bin/env python

import sys
from argparse import ArgumentParser

from pysolo_video import MonitoredArea


def get_mask_params(area_location):
    mask_params_by_area_location = {
        'upper_left': {
            'x1': 191.5,
            'x_span': 8,
            'x_gap': 3.75,
            'x_tilt': 0,

            'y1': 205,
            'y_len': 50,
            'y_sep': 2,
            'y_tilt': 0,
        },
        'lower_left': {
            'x1': 194,
            'x_span': 8,
            'x_gap': 3.75,
            'x_tilt': 0,

            'y1': 298,
            'y_len': 50,
            'y_sep': 2,
            'y_tilt': 0,
        },
        'upper_right': {
            'x1': 376,
            'x_span': 7.75,
            'x_gap': 4.2,
            'x_tilt': 0,

            'y1': 206,
            'y_len': 50,
            'y_sep': 2,
            'y_tilt': 0,
        },
        'lower_right': {
            'x1': 379,
            'x_span': 7.7,
            'x_gap': 4.1,
            'x_tilt': 0,

            'y1': 300,
            'y_len': 50,
            'y_sep': 2,
            'y_tilt': 0,
        }
    }
    return mask_params_by_area_location[area_location]


def get_mask_params_from_rois(arena):
    prev_roi = None
    n_rows = 0
    n_cols = 0

    x1 = 0
    x_span = 0
    x_gap = 0
    x_tilt = 0

    y1 = 0
    y_len = 0
    y_sep = 0
    y_tilt = 0

    row = 0
    for roi in arena.ROIS:
        (a, b, c, d) = roi
        if prev_roi is None:
            x1 = a[0]
            y1 = a[1]
            x_span = d[0] - a[0]
            y_len = b[1] - a[1]
            n_rows = 1
            n_cols = 1
        else:
            (pa, pb, pc, pd) = prev_roi
            row += 1
            if a[1] > pa[1] + y_len:
                # new row
                if n_rows == 1 and n_cols == 1:
                    x_tilt = a[0] - x1
                    y_sep = a[1] - y_len - y1
                if row + 1 > n_rows:
                    n_rows += 1
            else:
                if n_cols == 1:
                    y_tilt = a[1] - y1
                    x_gap = a[0] - x1 - x_span
                n_cols += 1
                row = 0

        prev_roi = roi

    mask_params = {
        'x1': x1,
        'x_span': x_span,
        'x_gap': x_gap,
        'x_tilt': x_tilt,

        'y1': y1,
        'y_len': y_len,
        'y_sep': y_sep,
        'y_tilt': y_tilt,
    }
    return mask_params, n_rows, n_cols


def create_mask(n_rows, n_cols, mask_params):
    x1 = mask_params['x1']
    x_span = mask_params['x_span']
    x_gap = mask_params['x_gap']
    x_tilt = mask_params['x_tilt']

    y1 = mask_params['y1']
    y_len = mask_params['y_len']
    y_sep = mask_params['y_sep']
    y_tilt = mask_params['y_tilt']

    arena = MonitoredArea()
    for col in range(0, n_cols):  # x-coordinates change through columns
        ay = y1 + col * y_tilt  # reset y-coordinate start of col
        by = ay + y_len
        cy = by
        dy = ay
        if col == 0:
            ax = x1
        else:
            ax = x1 + col * (x_span + x_gap)  # move over in x direction to start next column
        bx = ax
        cx = ax + x_span
        dx = cx
        for row in range(0, n_rows):  # y-coordinates change through rows
            arena.add_roi(
                (
                    (ax, ay),
                    (bx, by),
                    (cx, cy),
                    (dx, dy)
                )
            )
            ay = by + y_sep  # move down in y direction to start next row
            by = ay + y_len
            cy = by
            dy = ay
            ax = ax + x_tilt
            bx = ax
            cx = ax + x_span
            dx = cx
    return arena


def main():
    parser = ArgumentParser(usage='prog [options]')
    parser.add_argument('-m', '--mask-file', dest='mask_file', metavar='MASK_FILE',
                        help='The full name of the mask file')
    parser.add_argument('--rows', dest='rows', default=1, help='The number of rows')
    parser.add_argument('--cols', dest='cols', default=32, help='The number of cols')
    parser.add_argument('-r', '--region', dest='region',
                        required=True,
                        choices=['upper_left', 'lower_left', 'upper_right', 'lower_right'],
                        help='The name of the region for which to generate the mask')

    args = parser.parse_args()

    mask_params = get_mask_params(args.region)
    arena = create_mask(args.rows, args.cols, mask_params)
    arena.save_rois(args.mask_file)


if __name__ == '__main__':
    sys.exit(main())
