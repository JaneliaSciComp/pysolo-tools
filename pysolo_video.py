import _pickle as cPickle
import logging
import os
from datetime import datetime, timedelta
from os.path import dirname, exists

import cv2
import numpy as np

_logger = logging.getLogger('tracker')


class MonitoredArea():
    """
    The arena defines the space where the flies move
    Carries information about the ROI (coordinates defining each vial) and
    the number of flies in each vial

    The class monitor takes care of the camera
    The class arena takes care of the flies
    """

    def __init__(self,
                 track_type=1,
                 sleep_deprivation_flag=0,
                 fps=1,
                 aggregated_frames=1,
                 aggregated_frames_size=19,
                 tracking_data_buffer_size=9,
                 acq_time=None):
        """
        :param track_type: 
        :param sleep_deprivation_flag:
        :param fps: this values is the video acquisition rate and typically comes from the image source
        :param aggregated_frames: 
        :param tracking_data_buffer_size:
        :param acq_time: 
        """
        self._track_type = track_type
        self._sleep_deprivation_flag = sleep_deprivation_flag
        self.ROIS = []  # regions of interest
        self._beams = []  # beams: absolute coordinates
        self._points_to_track = []
        self._tracking_data_buffer_size = tracking_data_buffer_size if tracking_data_buffer_size > 0 else 1
        self._tracking_data_buffer = []
        self._tracking_data_buffer_index = 0
        self._fps = fps
        self._aggregated_frames = aggregated_frames if aggregated_frames > 0 else 1
        self._acq_time = datetime.now() if acq_time is None else acq_time
        self._roi_filter = None

        # shape ( rois, (x, y) ) - contains the coordinates of the current frame per ROI
        self._current_frame_fly_coord = np.zeros((1, 2), dtype=np.int)
        # shape ( rois, self._aggregated_frames+1, (x, y) ) - contains the coordinates of the frames that
        # need to be aggregated.
        self._aggregated_frames_size = aggregated_frames_size + 1 if aggregated_frames_size > 0 else 2
        self._aggregated_frames_fly_coord = np.zeros((1, self._aggregated_frames_size, 2), dtype=np.int)
        # the relative frame index from the last aggregation
        self._aggregated_frame_index = 0
        # index to the aggregated frames buffer - the reason for this is that if the number of aggregated frames is
        # too large these will drift apart
        self._aggregated_frames_buffer_index = 0
        self._first_position = (0, 0)

    def set_roi_filter(self, trackable_rois):
        """
        This is a testing method that allows to only track the specified rois
        :param trackable_rois: rois to track
        :return:
        """
        self._roi_filter = trackable_rois

    def is_roi_trackable(self, roi):
        return self._roi_filter is None or roi in self._roi_filter

    def set_output(self, filename, clear_if_exists=True):
        self._lineno = 0;
        self.output_filename = filename
        if self.output_filename:
            os.makedirs(dirname(self.output_filename), exist_ok=True)
        if exists(self.output_filename) and clear_if_exists:
            os.remove(self.output_filename)

    def add_fly_coords(self, roi_index, coords):
        """
        Add the provided coordinates to the existing list
        fly_index   int     the fly index number in the arena
        coords      (x,y)    the coordinates to add
        Called for every fly moving in every frame
        """
        fly_size = 15  # About 15 pixels at 640x480
        max_movement = fly_size * 100
        min_movement = fly_size / 3

        previous_position = tuple(self._current_frame_fly_coord[roi_index])
        is_first_movement = (previous_position == self._first_position)
        # coords is None if no blob was detected
        fly_coords = previous_position if coords is None else coords

        distance = self._distance(previous_position, fly_coords)
        if (distance > max_movement and not is_first_movement) or (distance < min_movement):
            # leave the position unchanged if the distance from the previous position is either too small or too big
            fly_coords = previous_position

        # Does a running average for the coordinates of the fly at each frame to _fly_coord_buffer
        # This way the shape of _fly_coord_buffer is always (n, (x,y)) and once a second we just have to add the (x,y)
        # values to _fly_period_end_coords, whose shape is (n, 60, (x,y))
        self._current_frame_fly_coord[roi_index] = fly_coords
        return fly_coords, distance

    def _distance(self, p1, p2):
        """
        Calculate the distance between two cartesian points
        """
        ((x1, y1), (x2, y2)) = (p1, p2)
        return np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

    def add_roi(self, roi, n_flies=1):
        self.ROIS.append(roi)
        self._points_to_track.append(n_flies)
        self._beams.append(self._get_midline(roi))

    def roi_to_rect(self, roi, scale=None):
        """
        Converts a ROI (a tuple of four points coordinates) into
        a Rect (a tuple of two points coordinates)
        """
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = roi
        lx = min([x1, x2, x3, x4])
        rx = max([x1, x2, x3, x4])
        uy = min([y1, y2, y3, y4])
        ly = max([y1, y2, y3, y4])
        scalef = (1, 1) if scale is None else scale
        return (
            (int(lx * scalef[0]), int(uy * scalef[1])),
            (int(rx * scalef[0]), int(ly * scalef[1]))
        )

    def roi_to_poly(self, roi, scale=None):
        """
        Converts a ROI (a tuple of four points coordinates) into
        a Rect (a tuple of two points coordinates)
        """
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = roi
        lx = min([x1, x2, x3, x4])
        rx = max([x1, x2, x3, x4])
        uy = min([y1, y2, y3, y4])
        ly = max([y1, y2, y3, y4])
        scalef = (1, 1) if scale is None else scale

        return [
            [int(lx * scalef[0]), int(ly * scalef[1])],
            [int(lx * scalef[0]), int(uy * scalef[1])],
            [int(rx * scalef[0]), int(uy * scalef[1])],
            [int(rx * scalef[0]), int(ly * scalef[1])]
        ]

    def _get_midline(self, roi):
        """
        Return the position of each ROI's midline
        Will automatically determine the orientation of the vial
        """
        (x1, y1), (x2, y2) = self.roi_to_rect(roi)
        horizontal = abs(x2 - x1) > abs(y2 - y1)
        if horizontal:
            xm = x1 + (x2 - x1) / 2
            return (xm, y1), (xm, y2)
        else:
            ym = y1 + (y2 - y1) / 2
            return (x1, ym), (x2, ym)

    def save_rois(self, filename):
        with open(filename, 'wb') as cf:
            cPickle.dump(self.ROIS, cf)
            cPickle.dump(self._points_to_track, cf)

    def load_rois(self, filename):
        """
        Load the crop data from the specified filename
        :param filename: name of the file to load the cropped region from
        :return:
        """
        self._mask_file = filename
        with open(filename, 'rb') as cf:
            self.ROIS = cPickle.load(cf)
            self._points_to_track = cPickle.load(cf)
        self._reset_data_buffers()
        for roi in self.ROIS:
            self._beams.append(self._get_midline(roi))

    def _reset_data_buffers(self):
        self._reset_current_frame_buffer()
        self._reset_aggregated_frames_buffers()

    def _reset_aggregated_frames_buffers(self):
        self._aggregated_frames_fly_coord = np.zeros((len(self.ROIS), self._aggregated_frames_size, 2), dtype=np.int)

    def _reset_current_frame_buffer(self):
        self._current_frame_fly_coord = np.zeros((len(self.ROIS), 2), dtype=np.int)

    def _reset_tracking_data_buffer(self):
        self._tracking_data_buffer = []
        self._tracking_data_buffer_index = 0
        self._aggregated_frame_index = 0

    def _shift_data_window(self, nframes):
        self._aggregated_frames_fly_coord = np.roll(self._aggregated_frames_fly_coord, (-nframes, 0), axis=(1, 0))
        self._aggregated_frames_buffer_index -= nframes

    def update_frame_activity(self, frame_time, scalef=None):
        self._aggregated_frames_fly_coord[:, self._aggregated_frames_buffer_index] = self._current_frame_fly_coord
        self._aggregated_frames_buffer_index += 1
        self._aggregated_frame_index += 1

        if self._aggregated_frame_index >= self._aggregated_frames:
            # aggregate the current buffers
            self.aggregate_activity(frame_time, scalef=scalef)
            # then
            if len(self._tracking_data_buffer) < self._tracking_data_buffer_size:
                # buffer the aggregated activity if there's room in the buffers
                self._tracking_data_buffer_index += 1
            else:
                # or dump the current data buffers to disk
                self.write_activity(frame_time, extend=self._extend)
            # reset the frame index
            self._aggregated_frame_index = 0
        elif self._aggregated_frames_buffer_index >= self._aggregated_frames_size:
            # the frame buffers reached the limit so aggregate the current buffers
            self.aggregate_activity(frame_time, scalef=scalef)

        # # prepare the frame coordinate buffer for the next frame
        self._reset_current_frame_buffer()

    def aggregate_activity(self, frame_time, scalef=None):
        if self._track_type == 0:
            values, _ = self._calculate_distances()
            activity = DistanceSum(frame_time, values)
        elif self._track_type == 1:
            values, _ = self._calculate_vbm(scale=scalef)
            activity = VirtualBeamCrossings(frame_time, values)
        elif self._track_type == 2:
            values, count = self._calculate_position()
            activity = AveragePosition(frame_time, values, count)
        else:
            raise ValueError('Invalid track type option: %d' % self._track_type)

        if len(self._tracking_data_buffer) <= self._tracking_data_buffer_index:
            self._tracking_data_buffer.append(activity)
        else:
            previous_activity = self._tracking_data_buffer[self._tracking_data_buffer_index]
            # combine previous activity with the current activity
            previous_activity.aggregate_with(activity)

    def write_activity(self, frame_time, extend=True):
        if self.output_filename:
            # monitor is active
            active = '1'
            # frames per seconds (FPS)
            damscan = self._fps
            # monitor with sleep deprivation capabilities?
            sleep_deprivation = self._sleep_deprivation_flag * 1
            # monitor number, not yet implemented
            monitor = '0'
            # unused
            unused = 0
            # is light on or off?
            light = '0'  # changed to 0 from ? for compatability with SCAMP
            # Expand the readings to 32 flies for compatibility reasons with trikinetics  - in our case 32 ROIs
            # since there's only one fly / ROI
            n_rois = len(self.ROIS)
            if extend and n_rois < 32:
                extension = 32 - n_rois
            else:
                extension = 0

            with open(self.output_filename, 'a') as ofh:
                for a in self._tracking_data_buffer:
                    self._lineno += 1
                    # frame timestamp
                    frame_dt = self._acq_time + timedelta(seconds=int(a.frame_time))
                    frame_dt_str = frame_dt.strftime('%d %b %y\t%H:%M:%S')

                    row_prefix = '%s\t' * 9 % (self._lineno, frame_dt_str,
                                               active, damscan, self._track_type,
                                               sleep_deprivation,
                                               monitor, unused, light)
                    ofh.write(row_prefix)
                    ofh.write(a.format_values(extension))
                    ofh.write('\n')
        self._reset_tracking_data_buffer()

    def _calculate_distances(self):
        """
        Motion is calculated as distance in px per minutes
        """
        # shift by one second left flies, seconds, (x,y)
        fs = np.roll(self._aggregated_frames_fly_coord, -1, axis=1)

        x = self._aggregated_frames_fly_coord[:, :self._aggregated_frames_buffer_index, 0]
        y = self._aggregated_frames_fly_coord[:, :self._aggregated_frames_buffer_index, 1]

        x1 = fs[:, :self._aggregated_frames_buffer_index, 0]
        y1 = fs[:, :self._aggregated_frames_buffer_index, 1]

        d = self._distance((x, y), (x1, y1))

        nframes = self._aggregated_frames_buffer_index - 1
        if nframes > 0:
            # we sum nframes only so that we don't have duplication
            values = d[:, :nframes].sum(axis=1)
            self._shift_data_window(nframes)
        else:
            values = np.zeros((len(self.ROIS)), dtype=np.int)

        return values, nframes

    def _calculate_vbm(self, scale=None):
        """
        Motion is calculated as virtual beam crossing
        Detects automatically beam orientation (vertical vs horizontal)
        """
        nframes = self._aggregated_frames_buffer_index - 1
        # the values.shape is (nframes, nrois)),
        values = np.zeros((len(self.ROIS)), dtype=np.int)
        if nframes > 0:
            roi_index = 0
            for fd, md in zip(self._aggregated_frames_fly_coord, self._relative_beams(scale=scale)):
                if self.is_roi_trackable(roi_index):
                    (mx1, my1), (mx2, my2) = md
                    horizontal = (mx1 == mx2)

                    fs = np.roll(fd, -1, 0)  # coordinates shifted to the following frame

                    x = fd[:self._aggregated_frames_buffer_index, 0]
                    y = fd[:self._aggregated_frames_buffer_index, 1]
                    x1 = fs[:self._aggregated_frames_buffer_index, 0]
                    y1 = fs[:self._aggregated_frames_buffer_index, 1]

                    if horizontal:
                        crosses = (x < mx1) * (x1 > mx1) + (x > mx1) * (x1 < mx1)
                    else:
                        crosses = (y < my1) * (y1 > my1) + (y > my1) * (y1 < my1)
                    # we sum nframes to eliminate duplication
                    values[roi_index] = crosses[:nframes].sum()
                else:
                    # the region is not tracked
                    values[roi_index] = 0

                roi_index += 1

            self._shift_data_window(nframes)

        return values, nframes

    def _relative_beams(self, scale=None):
        """
        Return the coordinates of the beam
        relative to the ROI to which they belong
        """
        scalef = (1, 1) if scale is None else scale
        beams = []
        for roi, beam in zip(self.ROIS, self._beams):
            rx, ry = self.roi_to_rect(roi)[0]
            (bx0, by0), (bx1, by1) = beam
            beams.append(
                (
                    ((bx0 - rx) * scalef[0], (by0 - ry) * scalef[1]),
                    ((bx1 - rx) * scalef[0], (by1 - ry) * scalef[1])
                )
            )
        return beams

    def _calculate_position(self, resolution=1):
        """
        Simply write out position of the fly at every time interval, as
        decided by "resolution" (seconds)
        """
        nframes = self._aggregated_frames_buffer_index - 1
        fs = np.roll(self._aggregated_frames_fly_coord, -1, axis=1)
        x = fs[:, :self._aggregated_frames_buffer_index, 0]
        y = fs[:, :self._aggregated_frames_buffer_index, 1]

        values = np.zeros((len(self.ROIS), 2), dtype=np.int)
        # we average nframes, which is 1 less the the buffer's end so that we don't have duplication
        if nframes > 0:
            values[:, 0] = x[:, :nframes].mean(axis=1)
            values[:, 1] = y[:, :nframes].mean(axis=1)
            self._shift_data_window(nframes)

        return values, nframes


class TrackingData():

    def __init__(self, frame_time, values):
        self.frame_time = frame_time
        self.values = values

    def aggregate_with(self, tracking_data):
        self.frame_time = tracking_data.frame_time
        self.values = self.combine_values(self.values, tracking_data.values)

    def combine_values(self, v1, v2):
        pass

    def format_values(self, extended_regions):
        if extended_regions > 0:
            return '\t'.join([str(v) for v in self.values] + ['0', ] * extended_regions)
        else:
            return '\t'.join([str(v) for v in self.values])


class VirtualBeamCrossings(TrackingData):

    def combine_values(self, v1, v2):
        return v1 + v2


class DistanceSum(TrackingData):

    def combine_values(self, v1, v2):
        return v1 + v2


class AveragePosition(TrackingData):

    def __init__(self, frame_time, values, n_values):
        super(AveragePosition, self).__init__(frame_time, values)
        self._n_values = n_values

    def aggregate_with(self, tracking_data):
        self.frame_time = tracking_data.frame_time
        new_values = (self.values * self._n_values + tracking_data.values * tracking_data._n_values) / (
                    self._n_values + tracking_data._n_values)
        self.values = new_values

    def format_values(self, extended_regions):
        if extended_regions > 0:
            return '\t'.join(['%s,%s' % (v[0], v[1]) for v in self.values] + ['0.0,0.0', ] * extended_regions)
        else:
            return '\t'.join(['%s,%s' % (v[0], v[1]) for v in self.values])


class ImageSource():

    def __init__(self, resolution=None, size=None):
        self._resolution = resolution
        self._size = size

    def get_scale(self):
        if self._size is not None and self._resolution and self._resolution[0] and self._resolution[1]:
            return (self._size[0] / self._resolution[0], self._size[1] / self._resolution[1])
        else:
            return None

    def get_size(self):
        return self._size

    def set_resolution(self, width, height):
        self._resolution = (width, height)

    def get_image(self):
        pass

    def get_start_time_in_seconds(self):
        pass

    def set_start_time_in_seconds(self, start_time):
        pass

    def get_end_time_in_seconds(self):
        pass

    def set_end_time_in_seconds(self, end_time):
        pass

    def get_current_frame_time_in_seconds(self):
        pass

    def close(self):
        pass


class MovieFile(ImageSource):

    def __init__(self, movie_file_path, start_msecs=None, end_msecs=None, resolution=None):
        """
        :param movie_file_path: path to the movie file
        :param step: distance between frames
        :param start: start frame. If None starts at first
        :param end: last frame. If None ends at last
        """

        # open the movie file
        self._capture = cv2.VideoCapture(movie_file_path)
        height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))

        super(MovieFile, self).__init__(resolution=resolution, size=(width, height))

        nframes = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self._fps = int(self._capture.get(cv2.CAP_PROP_FPS))

        self._movie_file_path = movie_file_path
        # set the start frame
        if start_msecs is None or start_msecs < 0:
            self._start = 0
        else:
            start_frame = int(start_msecs * self._fps / 1000)
            if start_frame < nframes:
                self._start = start_frame
            else:
                self._start = nframes
        # set the end frame
        if end_msecs is None or end_msecs < 0:
            self._end = nframes
        else:
            end_frame = int(end_msecs * self._fps / 1000)
            if end_frame < nframes:
                self._end = end_frame
            else:
                self._end = nframes
        # set the frame increment
        self._step = 1
        self._total_frames = nframes
        self._current_frame = self._start
        if self._current_frame != 0:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, self._current_frame)

    def get_fps(self):
        return self._fps

    def get_start(self):
        return self._start

    def get_end(self):
        return self._end

    def get_image(self):
        if self._current_frame < 0 or self._current_frame >= self._end:
            return False, -1, None
        else:
            current_frame = self._current_frame
            res = self._capture.read()
            if self._inc_current_frame() and self._step != 1:
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, self._current_frame)
            return res[0], current_frame, res[1]

    def _inc_current_frame(self):
        self._current_frame += self._step
        if self._current_frame < self._end:
            return True
        else:
            return False

    def update_frame_index(self, frame_index):
        if frame_index >= self._start and frame_index < self._end:
            self._current_frame = frame_index
        elif frame_index < self._start:
            self._current_frame = self._start
        else:
            self._current_frame = self._end
        self._capture.set(cv2.CAP_PROP_POS_FRAMES, self._current_frame)
        res = self._capture.read()
        return res[0], frame_index, res[1]

    def get_start_time_in_seconds(self):
        return self._start / self.get_fps()

    def set_start_time_in_seconds(self, start_time):
        start_frame = start_time * self.get_fps()
        if start_frame < 0:
            self._start = 0
        elif start_frame > self._total_frames:
            self._start = self._total_frames
        else:
            self._start = start_frame

    def get_end_time_in_seconds(self):
        return self._end / self.get_fps()

    def set_end_time_in_seconds(self, end_time):
        end_frame = end_time * self.get_fps()
        if end_frame < 0:
            self._end = 0
        elif end_frame > self._total_frames:
            self._end = self._total_frames
        else:
            self._end = end_frame

    def get_current_frame_time_in_seconds(self):
        frame_time_in_millis = self._capture.get(cv2.CAP_PROP_POS_MSEC)
        return frame_time_in_millis / 1000  # return the time in seconds

    def get_background(self, moving_alpha=0.2, gaussian_filter_size=(21, 21), gaussian_sigma=0.2):
        """
        The method attempts to get the background image using accumulate weighted method
        :param moving_alpha:
        :param gaussian_filter_size:
        :param gaussian_sigma:
        :return:
        """
        next_frame_res = self.get_image()
        frame_image = next_frame_res[2]
        # smooth the image to get rid of false positives
        frame_image = cv2.GaussianBlur(frame_image, gaussian_filter_size, gaussian_sigma)
        # initialize the moving average
        moving_average = np.float32(frame_image)
        while next_frame_res[0]:
            next_frame_res = self.get_image()
            if not next_frame_res[0]:
                break
            _logger.debug('Update moving average for %d' % next_frame_res[1])
            frame_image = next_frame_res[2]

            # smooth the image to get rid of false positives
            frame_image = cv2.GaussianBlur(frame_image, gaussian_filter_size, gaussian_sigma)
            cv2.accumulateWeighted(frame_image, moving_average, moving_alpha)

        background = cv2.convertScaleAbs(moving_average)
        return background

    def close(self):
        self._capture.release()


def prepare_monitored_areas(config, start_frame_msecs=None, end_frame_msecs=None):
    image_source = MovieFile(config.source,
                             start_msecs=start_frame_msecs,
                             end_msecs=end_frame_msecs,
                             resolution=config.image_size)

    def create_monitored_area(configured_area_index, configured_area):
        ma = MonitoredArea(track_type=configured_area.track_type,
                           sleep_deprivation_flag=1 if configured_area.sleep_deprived_flag else 0,
                           fps=image_source.get_fps(),
                           aggregated_frames=configured_area.get_aggregation_interval_in_frames(image_source.get_fps()),
                           acq_time=config.acq_time)
        ma.set_roi_filter(configured_area.tracked_rois_filter)
        ma.load_rois(configured_area.maskfile)
        ma.set_output(
            os.path.join(config.data_folder, 'Monitor%02d.txt' % configured_area_index)
        )
        return ma

    return image_source, [create_monitored_area(area_index, configured_area)
                          for area_index, configured_area in enumerate(config.get_monitored_areas())
                          if configured_area.track_flag]


def process_image_frames(image_source, monitored_areas, moving_alpha=0.1, gaussian_filter_size=(21, 21),
                         gaussian_sigma=1, cancel_callback=None, frame_pos_callback=None):
    moving_average = None

    def forever():
        return True

    not_cancelled = cancel_callback or forever

    while not_cancelled():
        frame_time_pos = image_source.get_current_frame_time_in_seconds()
        next_frame_res = image_source.get_image()
        if not next_frame_res[0]:
            break
        _logger.info('Process frame %d(frame time: %rs)' % (next_frame_res[1], frame_time_pos))

        if frame_pos_callback is not None:
            frame_pos_callback(next_frame_res[1])

        frame_image = next_frame_res[2]

        # smooth the image to get rid of false positives
        frame_image = cv2.GaussianBlur(frame_image, gaussian_filter_size, gaussian_sigma)

        if moving_average is None:
            moving_average = np.float32(frame_image)
        else:
            moving_average = cv2.accumulateWeighted(frame_image, moving_average, alpha=moving_alpha)

        temp_frame = cv2.convertScaleAbs(moving_average)

        background_diff = cv2.subtract(temp_frame, frame_image)  # subtract the background
        grey_image = cv2.cvtColor(background_diff, cv2.COLOR_BGR2GRAY)

        binary_image = cv2.threshold(grey_image, 20, 255, cv2.THRESH_BINARY)[1]
        binary_image = cv2.dilate(binary_image, None, iterations=2)
        binary_image = cv2.erode(binary_image, None, iterations=2)

        for area_index, monitored_area in enumerate(monitored_areas):
            for roi_index, roi in enumerate(monitored_area.ROIS):
                if monitored_area.is_roi_trackable(roi_index):
                    process_roi(binary_image, monitored_area, roi, roi_index, image_source.get_scale())

            # prepare the frame coordinates buffer for the next frame
            monitored_area.update_frame_activity(frame_time_pos, scalef=image_source.get_scale())

    # write the remaining activity that is still in memory
    for area_index, monitored_area in enumerate(monitored_areas):
        # aggregate whatever is left in the buffers
        monitored_area.aggregate_activity(frame_time_pos, scalef=image_source.get_scale())
        # then write them out to disk
        monitored_area.write_activity(frame_time_pos)

    return True


def draw_roi(roi_mask, roi):
    cv2.fillPoly(roi_mask, [roi], color=[255, 255, 255])


def process_roi(image, monitored_area, roi, roi_index, scalef):
    roi_mask = np.zeros(image.shape, np.uint8)
    (offset_x, offset_y), _ = monitored_area.roi_to_rect(roi, scalef)
    draw_roi(roi_mask, np.array(monitored_area.roi_to_poly(roi, scalef)))

    image_roi = cv2.bitwise_and(image, image, mask=roi_mask)
    # get the contours relative to the upper left corner of the ROI
    fly_cnts = cv2.findContours(image_roi.copy(), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE,
                                offset=(-offset_x, -offset_y))

    fly_coords = None
    for fly_contour in fly_cnts[1]:
        fly_contour_moments = cv2.moments(fly_contour)
        area = fly_contour_moments['m00']
        if area > 0:
            fly_coords = (fly_contour_moments['m10'] / fly_contour_moments['m00'],
                          fly_contour_moments['m01'] / fly_contour_moments['m00'])
        else:
            bound_rect = cv2.boundingRect(fly_contour)
            pt1 = (bound_rect[0], bound_rect[1])
            pt2 = (bound_rect[0] + bound_rect[2], bound_rect[1] + bound_rect[3])
            fly_coords = (pt1[0] + (pt2[0] - pt1[0]) / 2, pt1[1] + (pt2[1] - pt1[1]) / 2)
            area = (pt2[0] - pt1[0]) * (pt2[1] - pt1[1])

        if area > 400 * scalef[0] * scalef[1]:
            fly_coords = None

    return monitored_area.add_fly_coords(roi_index, fly_coords)
