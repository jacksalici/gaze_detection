from .components.face_landmark import FaceLandmarkTracking, FaceLandmarkK
from .components.pupil_detection_means_gradients import PupilDetection as PupilDetectionMeansGradient
from .components.pupil_detection_filtering import PupilDetection as PupilDetectionFiltering
from .components.pnp_solver import PnPSolver

import cv2
import numpy as np
import os
import serial
import struct

FILTERING = "filtering"
GRAD_MEANS = "grad_means"
NO_PUPIL_DETECTION = "no_pupil"

EYE_RATIO_MULTIPLIER = 100

FACE_FACING_SENSIBILITY = 20
GAZE_FACING_SENSIBILITY = 5

k = FaceLandmarkK()

class GazeDetection():
    def __init__(self, predictor_path: str = None, 
                 pupil_detection_mode: str = GRAD_MEANS, 
                 video: bool = True, 
                 image_path: str = None,
                 visual_verbose: bool = False,
                 save_image: bool = False,
                 print_on_serial: bool = True,
                 serial_writing_step: float = 5,
                 serial_port: str = "/dev/tty.usbmodem1201",
                 annotate_image: bool = False,
                 crop_frame: bool = True,
                 crop_frame_paddings: tuple = (0.5,0,0.15,0), #top, right, bottom, left / [0:1]
                 face_facing_sensibility: int = FACE_FACING_SENSIBILITY,
                 eye_frame_padding: tuple = (0, 2), #horizontal, vertical
                 ) -> None:
        """Detects gaze and/or pose estimation using various methods.

        Args:
            predictor_path (str, optional): Path to dlib predictor. Defaults to os.path.join('resources', 'predictors', 'shape_predictor_68_face_landmarks.dat').
            pupil_detection_mode (str, optional): Choose between "filtering"/"grad_means"/"no_pupil": respectively, it uses filters to estimate pupil prection, or it uses means of gradient method by Timm 2011 or it does not compute them. Defaults to "grad_means".
            video (bool, optional): The input stream can be directly captured inside the class. If false, you have to pass an image path to the next attribute or frame by frame calling the 'detect' method. Defaults to True.
            image_path (str, optional): Valid if the video=False. Input image for a single detection. If you want to call the 'detect' method in your pipeline, keep defaults option. Defaults to None.
            visual_verbose (bool, optional): If true, it will create more windows to show the partial steps. Useful only if pupil_detection_mode="filtering". Defaults to False.
            save_image (bool, optional): If image_path is provided, if True, it will save the output image in the same path (not override). Defaults to False.
            print_on_serial (bool, optional): Useful if you want to move a stepper motor to track images, if true, a float will be writed on the serial port. Defaults to True.
            serial_port (str, optional): Serial port to use for writing. Defaults to "/dev/tty.usbmodem1201".
            serial_writing_step (float, optional): Value printed on the serial to follow the robot. Each frame it will print that value until the biggest faces is centered. Default to 5.
            annotate_image (bool, optional): If true, angles and landmark will be added to the image. Defaults to False.
            crop_frame (bool, optional): If true, the return frame will be cropped on the biggest face. Defaults to True.
            crop_frame_paddings (tuple, optional): Padding of the face cropped frame. The tuple has the format (top, right, bottom, left), values of 0 mean no padding, values of 1 mean a padding on that edge equal to the size of the frame in the perpendicular dimension. Defaults to (0.5,0,0.15,0).
            face_facing_sensibility (int, oprional): angle sensibility for which the face will be considered facing. Defaults to 20. 
            eye_frame_padding (tuple, optional): (horizontal, vertical) padding, in pixel of the eye applied before the pupil detection. Defaults to (0, 2).
        """
    
        
        try:
            if predictor_path != None:
                self.landmark_tracking = FaceLandmarkTracking(predictor_path)
            else:
                self.landmark_tracking = FaceLandmarkTracking(os.path.join('src', 'resources', 'predictors', 'shape_predictor_68_face_landmarks.dat'))
        except:
            raise Exception("ERROR: Face landmark not found, please download and extract it from http://dlib.net/files/shape_predictor_68_face_landmarks.dat.bz2 and pass the .dat file as argument.")

        self.pupil_detection_mode = pupil_detection_mode
        if (pupil_detection_mode == GRAD_MEANS):
            self.pupil_detection = PupilDetectionMeansGradient()
        elif(pupil_detection_mode == FILTERING):
            self.pupil_detection = PupilDetectionFiltering(visual_verbose)
        else:
            self.pupil_detection = None

        # to use the calibration (np.load('calib_results.npz'))
        self.pnp_solver = PnPSolver()


        self.face_facing = False
        self.gaze_facing = False
        
        self.print_on_serial = print_on_serial
        self.annotate_image = annotate_image
        self.eye_frame_padding = eye_frame_padding
        self.crop_frame = crop_frame
        self.crop_frame_paddings = crop_frame_paddings
        self.face_facing_sensibility = face_facing_sensibility
        
        try:
            if print_on_serial:
                self.serial_port = serial.Serial(serial_port, 9600)
                self.serial_writing_step = serial_writing_step
        except:
            print_on_serial = False
            

        if video:
            vid = cv2.VideoCapture(0)
            while(True):
                ret, frame = vid.read()

                return_frame, _ = self.detect(frame)

                cv2.imshow('frame',  return_frame)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            vid.release()
        else:
            if image_path != None:
                frame = cv2.imread(image_path)
                
                frame, _ = self.detect(frame)
                
                cv2.imshow('frame', frame)
                
                if save_image:
                    cv2.imwrite(os.path.splitext(image_path)[
                                0] + '_edited' + os.path.splitext(image_path)[1], frame)
                cv2.waitKey()

    def detect(self, frame: np.ndarray, return_info: bool = False):
        """Detection method. Note that it is configurated during class initialization.

        Args:
            frame (np.ndarray): Single image to compute, BGR color channel.
            return_info (bool, optional): If true, the function also returns a dictionary with pose estimation and pupil localization.

        Returns:
            frame (np.ndarray), gaze_facing(bool): Frame computed, a gaze facing boolean value. If retrun_info = True, it also returns info(dict) 
        """

        framebg = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        
        biggest_face_index = 0
        biggest_face_area = 0
        
        saved_info = {}

        # Compute detection for each face
        faces = self.landmark_tracking.face_analysis(framebg)
        for face_index, face in enumerate (faces):

            image_points = np.array([
                face.get(k.NOSE),
                face.get(k.CHIN),
                face.get(k.EYE_L_OUT),
                face.get(k.EYE_R_OUT),
                face.get(k.MOUTH_L),
                face.get(k.MOUTH_R),
            ], dtype="double")
            
            #pose n perspective solver
            nose_end_point2D, pitch, yaw, roll = self.pnp_solver.pose(
                frame.shape, image_points)

            saved_info["pitch"] = pitch
            saved_info["yaw"] = yaw
            saved_info["roll"] = roll

            face_facing = False

            if abs(pitch) < self.face_facing_sensibility and abs(yaw) < self.face_facing_sensibility:
                face_facing = True

            gaze_facing = face_facing
            
            
            #pupil detection
            if self.pupil_detection != None:
                eyes = [framebg[
                face.get(k.EYE_L_TOP)[1] - self.eye_frame_padding[1]:
                    face.get(k.EYE_L_BOTTOM)[1] + self.eye_frame_padding[1],
                face.get(k.EYE_L_IN)[0] - self.eye_frame_padding[0]:
                    face.get(k.EYE_L_OUT)[0] + self.eye_frame_padding[0]],
                    framebg[
                face.get(k.EYE_R_TOP)[1]-self.eye_frame_padding[1]:
                    face.get(k.EYE_R_BOTTOM)[1]+self.eye_frame_padding[1],
                face.get(k.EYE_R_OUT)[0]-self.eye_frame_padding[0]:
                    face.get(k.EYE_R_IN)[0]+self.eye_frame_padding[0]]
                    ]

                pupil_l_x, pupil_l_y = self.pupil_detection.detect_pupil(eyes[0])
                pupil_r_x, pupil_r_y = self.pupil_detection.detect_pupil(eyes[1])
                
                
                #relative localization
                pupil_l_y, pupil_l_x = face.get(k.EYE_L_TOP)[
                    1] - self.eye_frame_padding[1] + pupil_l_y, face.get(k.EYE_L_IN)[0] - self.eye_frame_padding[0] + pupil_l_x
                pupil_r_y, pupil_r_x = face.get(k.EYE_R_TOP)[
                    1] - self.eye_frame_padding[1] + pupil_r_y, face.get(k.EYE_R_OUT)[0] - self.eye_frame_padding[0] + pupil_r_x


                saved_info["l_eye"] = [pupil_l_x, pupil_l_y]
                saved_info["r_eye"] = [pupil_r_x, pupil_r_y]
                # horizontal ratio that expresses how centered the pupil is within the eyes, from -0.5 to 0.5, 0 is center.
                pupil_l_center_h_ratio = round((pupil_l_x - face.get(k.EYE_L_IN)[0]) / (
                    face.get(k.EYE_L_OUT)[0] - face.get(k.EYE_L_IN)[0]) - 0.5, 2)
                pupil_r_center_h_ratio = round((pupil_r_x - face.get(k.EYE_R_OUT)[0]) / (
                    face.get(k.EYE_R_IN)[0] - face.get(k.EYE_R_OUT)[0]) - 0.5, 2)
            
                #facing computation
                if face_facing:
                    if max(abs(pupil_l_center_h_ratio), abs(pupil_r_center_h_ratio))<(self.face_facing_sensibility/EYE_RATIO_MULTIPLIER):
                        gaze_facing = True
                    else:
                        gaze_facing = False
                elif yaw < 0 and abs(2*abs(pupil_l_center_h_ratio) * EYE_RATIO_MULTIPLIER - abs(yaw)) < GAZE_FACING_SENSIBILITY:
                    gaze_facing = True
                elif yaw > 0 and abs(2*abs(pupil_r_center_h_ratio) * EYE_RATIO_MULTIPLIER - abs(yaw)) < GAZE_FACING_SENSIBILITY:
                    gaze_facing = True

            #check the area of each face and find the max one
            current_face_area = face.get(k.BOX)[2]*face.get(k.BOX)[3]
            if current_face_area > biggest_face_area:
                biggest_face_area = current_face_area
                biggest_face_index = face_index
                
                
            #printing stuffs
            if self.annotate_image:
                try:
                    cv2.rectangle(frame, (face.get(k.BOX)[0], face.get(k.BOX)[1]), (face.get(k.BOX)[
                        0]+face.get(k.BOX)[2], face.get(k.BOX)[1]+face.get(k.BOX)[3]), (255, 0, 255), 2)

                    cv2.rectangle(frame, (face.get(k.EYE_L_IN)[0]-self.eye_frame_padding[0],
                                        face.get(k.EYE_L_TOP)[1]-self.eye_frame_padding[1]),
                                (face.get(k.EYE_L_OUT)[0]+self.eye_frame_padding[0],
                                face.get(k.EYE_L_BOTTOM)[1]+self.eye_frame_padding[1]),
                                (255, 0, 255), 2)
                    cv2.rectangle(frame, (face.get(k.EYE_R_OUT)[0]-self.eye_frame_padding[0],
                                        face.get(k.EYE_R_TOP)[1]-self.eye_frame_padding[1]),
                                (face.get(k.EYE_R_IN)[0]+self.eye_frame_padding[0],
                                face.get(k.EYE_R_BOTTOM)[1]+self.eye_frame_padding[1]),
                                (255, 0, 255), 2)
                    
                    for p in list(face.values())[1:]:
                        cv2.circle(frame, (int(p[0]), int(p[1])),
                                2, (255, 255, 0), -1)
                except:
                    print("WARNING: Error during info display")

                if (self.pupil_detection != None):
                    try:
                        cv2.circle(frame, (pupil_l_x, pupil_l_y),
                                10, (0, 255, 255), 2)

                        cv2.circle(frame, (pupil_r_x, pupil_r_y),
                                10, (0, 255, 255), 2)

                        cv2.putText(frame, f"Pupil horizzontal ratios: {pupil_r_center_h_ratio}, {pupil_l_center_h_ratio}", (face.get(k.EYE_R_OUT)[0], 160),
                                    1, 1, (255, 255, 255), 1, cv2.LINE_AA)
                    except:
                        print("WARNING: Error during info display")

                try:
                    frame = cv2.line(frame, tuple(image_points[0].ravel().astype(int)), tuple(
                        nose_end_point2D[0].ravel().astype(int)), (255, 0, 0), 2)
                    frame = cv2.line(frame, tuple(image_points[0].ravel().astype(int)), tuple(
                        nose_end_point2D[1].ravel().astype(int)), (0, 255, 0), 2)
                    frame = cv2.line(frame, tuple(image_points[0].ravel().astype(int)), tuple(
                        nose_end_point2D[2].ravel().astype(int)), (0, 0, 255), 2)

                    if roll and pitch and yaw:
                        cv2.putText(frame, "Roll: " + str(round(roll)), (face.get(k.EYE_R_OUT)[0], 100),
                                    1, 1, (255, 255, 255), 1, cv2.LINE_AA)
                        cv2.putText(frame, "Pitch: " + str(round(pitch)), (face.get(k.EYE_R_OUT)[0], 120),
                                    1, 1, (255, 255, 255), 1, cv2.LINE_AA)
                        cv2.putText(frame, "Yaw: " + str(round(yaw)), (face.get(k.EYE_R_OUT)[0], 140),
                                    1, 1, (255, 255, 255), 1, cv2.LINE_AA)

                except:
                    print("WARNING: Error during info display")

                try:
                    cv2.putText(frame, "Gaze facing camera: " + str(int(gaze_facing)), (face.get(k.EYE_R_OUT)[0], 200),
                                1, 1, (255, 255, 255), 1, cv2.LINE_AA)
                except:
                    print("WARNING: Error during info display")
                
                try:
                    cv2.putText(frame, "Face facing camera: " + str(int(face_facing)), (face.get(k.EYE_R_OUT)[0], 180),
                                1, 1, (255, 255, 255), 1, cv2.LINE_AA)
                except:
                    print("WARNING: Error during info display")

                
        if len(faces)>0:
            
            frame_center = frame.shape[1]/2
            (fx, fy, fw, fh) = faces[biggest_face_index].get(k.BOX) 
            face_center = fx + fw/2 
            
            try:
                if self.print_on_serial:
                    if face_center<frame_center-200:
                        self.serial_port.write(struct.pack('f', -self.serial_writing_step))
                    elif face_center>frame_center+200:
                        self.serial_port.write(struct.pack('f', self.serial_writing_step))
            except:
                print("WARNING: Error writing on serial")
                
            
            if self.crop_frame:
                frame = frame[max(int(fy-fh*self.crop_frame_paddings[0]),0):min(fy+int(fh*(1+self.crop_frame_paddings[2])), frame.shape[0]), 
                             max(int(fx-fw*self.crop_frame_paddings[3]),0):min(fx+int(fw*(1+self.crop_frame_paddings[1])), frame.shape[1])]

        else:
            gaze_facing = False
        
        if return_info == False:
            return frame, gaze_facing
        else:
            return frame, gaze_facing, saved_info

