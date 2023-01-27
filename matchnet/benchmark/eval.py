import argparse
import glob
import os
import pickle

import cv2
import matplotlib.pyplot as plt
import numpy as np
import open3d as o3d
import logging
from tqdm import tqdm
from ipdb import set_trace

from matchnet import config
from matchnet.benchmark.metrics import *
from matchnet.code.utils import common, misc
from matchnet.code.utils.pointcloud import transform_xyz
from matchnet.code.baseline.eval_baseline import cal_pose

logger=logging.getLogger(__name__)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Algorithm on Train Partition")
    parser.add_argument("--pose_pkl", type=str,default = "bear_poses.pkl")
    parser.add_argument("--debug", type=lambda s: s.lower() in ["1", "true"], default=False)
    args, unparsed = parser.parse_known_args()

    kit_dirs = glob.glob("../datasets" + "/*")
    kit_dirs = list(filter(lambda file_name:file_name[-4:] != ".pkl", kit_dirs))
    dump_dir = "../dump/"
    all_poses = pickle.load(open(os.path.join(dump_dir, args.pose_pkl), "rb"))
    use_color = True
    accuracies = {}
    for kit_idx, data_dir in enumerate(kit_dirs):
        # if data_dir.split("/")[-1] == "deodorants":
        #     continue
        print("{}/{}".format(kit_idx+1, len(kit_dirs)))
        if "ORB" in args.pose_pkl:
            estimated_poses = all_poses[data_dir.split("/")[-1]]
        else:
            estimated_poses = all_poses
        test_dir = os.path.join(data_dir,)
        test_foldernames = glob.glob(test_dir + "/*")
        test_foldernames = list(filter(lambda file_name:file_name[-4:] != ".pkl", test_foldernames))
        test_foldernames.sort(key=lambda x: int(x.split("/")[-1]))
        add_errors = []
        reproj_errors = []
        translational_errors = []
        rotational_errors = []
        for folder_idx, folder in enumerate(test_foldernames):
            # load ground truth pose
            # intr = np.loadtxt(os.path.join(data_dir, "intr.txt"))
            # extr = np.loadtxt(os.path.join(data_dir, "extr.txt"))
            depth_f = common.depthload(folder, init = False)
            color_f = common.colorload(folder, init = False,use_color=use_color)
            H, W = depth_f.shape
            info_dict = pickle.load(open(os.path.join(folder, "info_dict.pkl"),"rb"))
            obj_mask = info_dict["obj"][-1]
            # init_pose = np.loadtxt(os.path.join(folder, "init_pose.txt"))
            # final_pose = np.loadtxt(os.path.join(folder, "final_pose.txt"))
            init_pose = cal_pose(info_dict, init=True, depth_image=depth_f)
            final_pose = cal_pose(info_dict, init=False, depth_image=depth_f)
            true_pose = np.linalg.inv(final_pose @ np.linalg.inv(init_pose))
            # load estimated pose
            estimated_pose = estimated_poses[folder_idx]

            if np.isnan(np.min(estimated_pose)):
                add_errors.append(np.nan)
                reproj_errors.append(np.nan)
                rotational_errors.append(np.nan)
                translational_errors.append(np.nan)
                continue

            # trim object mask
            depth_vals = depth_f[obj_mask[:, 0], obj_mask[:, 1]]
            valid_ds = depth_vals >= depth_vals.mean()
            mask = np.zeros_like(depth_f)
            mask[obj_mask[valid_ds][:, 0], obj_mask[valid_ds][:, 1]] = 1
            mask = misc.largest_cc(mask)
            valid_mask = np.vstack(np.where(mask == 1)).T
            tset = set([tuple(x) for x in valid_mask])
            for i in range(len(valid_ds)):
                is_valid = valid_ds[i]
                if is_valid:
                    tidx = obj_mask[i]
                    if tuple(tidx) not in tset:
                        valid_ds[i] = False
            obj_mask = obj_mask[valid_ds]

            zs = depth_f[obj_mask[:, 0], obj_mask[:, 1]].reshape(-1, 1)
            obj_xyz = np.hstack([obj_mask, zs])
            obj_xyz[:, [0, 1]] = obj_xyz[:, [1, 0]]
            obj_xyz[:, 0] = (obj_xyz[:, 0] * config.HEIGHTMAP_RES) + config.VIEW_BOUNDS[0, 0]
            obj_xyz[:, 1] = (obj_xyz[:, 1] * config.HEIGHTMAP_RES) + config.VIEW_BOUNDS[1, 0]

            if args.debug:
                zs = depth_f[obj_mask[:, 0], obj_mask[:, 1]].reshape(-1, 1)
                mask_xyz = np.hstack([obj_mask, zs])
                mask_xyz[:, [0, 1]] = mask_xyz[:, [1, 0]]
                mask_xyz[:, 0] = (mask_xyz[:, 0] * config.HEIGHTMAP_RES) + config.VIEW_BOUNDS[0, 0]
                mask_xyz[:, 1] = (mask_xyz[:, 1] * config.HEIGHTMAP_RES) + config.VIEW_BOUNDS[1, 0]
                mask_xyz = transform_xyz(mask_xyz, estimated_pose)
                mask_xyz[:, 0] = (mask_xyz[:, 0] - config.VIEW_BOUNDS[0, 0]) / config.HEIGHTMAP_RES
                mask_xyz[:, 1] = (mask_xyz[:, 1] - config.VIEW_BOUNDS[1, 0]) / config.HEIGHTMAP_RES
                hole_idxs = mask_xyz[:, [1, 0]]
                mask_xyz = np.hstack([obj_mask, zs])
                mask_xyz[:, [0, 1]] = mask_xyz[:, [1, 0]]
                mask_xyz[:, 0] = (mask_xyz[:, 0] * config.HEIGHTMAP_RES) + config.VIEW_BOUNDS[0, 0]
                mask_xyz[:, 1] = (mask_xyz[:, 1] * config.HEIGHTMAP_RES) + config.VIEW_BOUNDS[1, 0]
                true_xyz = transform_xyz(mask_xyz, true_pose)
                true_xyz[:, 0] = (true_xyz[:, 0] - config.VIEW_BOUNDS[0, 0]) / config.HEIGHTMAP_RES
                true_xyz[:, 1] = (true_xyz[:, 1] - config.VIEW_BOUNDS[1, 0]) / config.HEIGHTMAP_RES
                true_idxs = true_xyz[:, [1, 0]]
                fig, axes = plt.subplots(1, 2)
                axes[0].imshow(color_f)
                axes[0].scatter(hole_idxs[:, 1], hole_idxs[:, 0])
                axes[1].imshow(color_f)
                axes[1].scatter(true_idxs[:, 1], true_idxs[:, 0])
                for ax in axes:
                    ax.axis('off')
                plt.show()

                obj_xyz_pred = transform_xyz(obj_xyz, estimated_pose)
                obj_xyz_true = transform_xyz(obj_xyz, true_pose)
                pcs = []
                pts = obj_xyz_pred[:, :3].copy().astype(np.float64)
                pc = o3d.PointCloud()
                pc.points = o3d.Vector3dVector(pts)
                pcs.append(pc)
                pts = obj_xyz_true[:, :3].copy().astype(np.float64)
                pc = o3d.PointCloud()
                pc.points = o3d.Vector3dVector(pts)
                pcs.append(pc)
                o3d.draw_geometries(pcs)

            # compute metric
            add_err = compute_ADD(true_pose, estimated_pose, obj_xyz)
            # reproj_err = reprojection_error(true_pose, estimated_pose, obj_xyz, config.VIEW_BOUNDS, config.HEIGHTMAP_RES)
            reproj_err = reprojection_error(true_pose, estimated_pose, obj_xyz, None, None)
            gt_final_pose = init_pose.copy()
            est_final_pose = estimated_pose @ final_pose
            rot_err = rotational_error(gt_final_pose[:3, :3], est_final_pose[:3, :3])
            trans_err = translational_error(gt_final_pose[:3, 3], est_final_pose[:3, 3])

            # if data_dir.split("/")[-1] == "fruits" and folder_idx in config.FRUIT_IDXS:
            #     rot_err = 0

            add_errors.append(add_err)
            reproj_errors.append(reproj_err)
            rotational_errors.append(rot_err)
            translational_errors.append(trans_err)

        acc = {}
        acc["err_add"] = add_errors
        acc["err_reproj"] = reproj_errors
        acc["err_rot"] = rotational_errors
        acc["err_trans"] = translational_errors
        accuracies[data_dir.split("/")[-1]] = acc

    with open(os.path.join(dump_dir, "{}_acc.pkl".format(args.pose_pkl.split("_")[0])), "wb") as fp:
        pickle.dump(accuracies, fp)
    logger.warning("Save pkl at %s",os.path.join(dump_dir, "{}_acc.pkl".format(args.pose_pkl.split("_")[0])))