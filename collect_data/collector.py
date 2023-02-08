import os
import cv2
from suction_mask import get_obj_coord_with_mask_2d
from overlap_detect import OverlapDetector
from tools.hardware.camera.cam_d435i import initial_camera, get_curr_image
from tools.hardware.arm.calibration import arm_suction_img,getM,gen_coords,b2c
from tools.hardware.arm.operation import MyRobot
import random
import logging
import pickle
from tqdm import tqdm

logger = logging.getLogger(__file__)
KIT_NUM = {"rabbit":4,"whale":5,"bear":6}
# KIT_NAME = 0
DUMP_PATH = "dataset0108_raw"

INIT_POINT = "init_point"
FINAL_POINT = "final_point"
FINAL_POINT3D = "final_point3d"
ROTATION = "rotation"

def find_coord_to_place(robot,mask, coord):
    count = 0
    while True:
        random_place_3d = gen_coords(method = "random",epoch = 1) #TODO:robot rndom
        random_theta = random.uniform(0,360)
        random_place_2d = robot.b2c(random_place_3d)
        if  not detector.detect_overlap(mask, random_theta, coord, random_place_2d):
            return random_place_3d,random_place_2d,random_theta
        count += 1
        if count >= 10:
            logger.warning("can't find place after %s's try.",count)


if __name__ == "__main__":
    kit_name = "bear"
    robot_id = 18
    dump_path = os.path.join(os.getcwd(),DUMP_PATH,KIT_NUM[kit_name])
    if not os.path.exists(dump_path):
        os.makedirs(dump_path)
    pipeline,align = initial_camera()
    robot = MyRobot(robot_id,mode="wooden")
    compare_color, compare_depth = get_curr_image(pipeline,align)
    cv2.imwrite(os.path.join(dump_path,"compare_color.png"),compare_color)
    cv2.imwrite(os.path.join(dump_path,"compare_depth.png"),compare_depth)
    # compare_depth  =  cv2.imread(os.path.join("20230108_compare","train", f"depth0.png"), cv2.IMREAD_GRAYSCALE)
    detector = OverlapDetector(compare_depth.shape) 
    arm_info_dict = {}
    i = 0
    for _ in tqdm(range(100)):
        # init status
        kit_start = i
        color_image, depth_image = get_curr_image(pipeline,align)
        cv2.imwrite(os.path.join(dump_path,f"color{i}.png"),color_image)
        cv2.imwrite(os.path.join(dump_path,f"depth{i}.png"),depth_image)
        arm_info_dict[i] = None
        pickle.dump(arm_info_dict, open(os.path.join(dump_path,f"arm_info_dict.png"), "wb"))
        obj_coord, mask_list = get_obj_coord_with_mask_2d(compare_depth,depth_image, color_image, KIT_NUM[kit_name]+2)
        assert len(obj_coord) == KIT_NUM[kit_name]

        # dissamble stage
        random_idx = random.shuffle(range(len(KIT_NUM[kit_name])))
        for one_random_idx in random_idx:
            i += i
            uv_coord, radius = obj_coord[random_idx]
            selected_mask = mask_list[random_idx]
            print("len(obj_coord) = ",len(obj_coord))
            # fake move
            count = 0
            robot.arm_to_coord2d(uv_coord, 0,place=False,to_kit=True) #suc from kit
            # find coord to place
            random_place_3d,random_place_2d,random_theta = find_coord_to_place(selected_mask, uv_coord)
            arm_info_dict[i] = {INIT_POINT:uv_coord, FINAL_POINT:random_place_2d,ROTATION:random_theta,FINAL_POINT3D:random_place_3d}
            robot.arm_to_coord(random_place_3d,random_theta, place=True) # place to outer
            cv2.imwrite(os.path.join(dump_path,f"color{i}.png"),color_image)
            cv2.imwrite(os.path.join(dump_path,f"depth{i}.png"),depth_image)
            pickle.dump(arm_info_dict, open(os.path.join(dump_path,f"arm_info_dict.png"), "wb"))

        # asamble stage
        for time_step in range(kit_start,kit_start +KIT_NUM[kit_name]):
            cur_step_info = arm_info_dict[time_step]
            robot.arm_to_coord(cur_step_info[FINAL_POINT3D], cur_step_info[ROTATION], place = False) # suc outer obj
            robot.arm_to_coord2d(cur_step_info[INIT_POINT], 0, place=True, to_kit=True) # place to kit