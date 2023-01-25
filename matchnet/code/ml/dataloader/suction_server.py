"""The suction dataset.
"""

import glob
import logging
import multiprocessing
import os
import pickle

import cv2
import matplotlib.pyplot as plt
import numpy as np
import torch

from pathlib import Path
from PIL import Image
from skimage.draw import circle
from skimage.measure import label
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

from tools.matrix import gen_rot_mtx_anticlockwise
from matchnet import config
from matchnet.code.utils import misc


def remove_small_area(mask,area_th):
    
    contours, hierarch = cv2.findContours(mask.astype('uint8'), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for i in range(len(contours)):
        area = cv2.contourArea(contours[i])
        if area < area_th:
            cv2.drawContours(mask, [contours[i]], 0, 0, -1)
    return mask

def remove_surrounding_white(mask,visual):
    '''
    在mask中，去掉贴着图像边缘的白色部分（通常是背景）
    :param mask:
    :param visual: 可视化
    :return: mask：处理后的mask
    '''
    h,w = mask.shape
    labels = label(mask)
    if visual:
        cv2.imshow('labels going to remove_surrounding_white',(labels*40).astype('uint8'))
    num = np.max(labels)
    if visual:
        print('num in remove_surrounding_white',num)
    if num > 1:#如果只有一个连通域，不需要处理
        for i in range(num):
            domain = np.where(labels==i+1,1,0)
            if visual:
                cv2.imshow('domain in remove_surrounding_white',(domain.astype('uint8'))*255)
            rmin,rmax,cmin,cmax = mask2bbox(domain)
            if rmin ==0 or rmax == h-1 or cmin == 0 or cmax == w-1:
                labels = np.where(labels == i+1 , 0 , labels)
        mask = np.where(labels !=0,mask,0)
        if visual:
            cv2.imshow('mask in remove_surrounding_white',mask)
    return mask


def remove_inner_black(mask,visual):
    '''
    在mask中去掉白色部分中间的黑色
    :param mask:
    :param visual: 可视化
    :return: mask：处理后的mask
    '''
    h, w = mask.shape
    mask = 255 - mask
    labels = label(mask)
    if visual:
        cv2.imshow('labels going to remove_inner_black', (labels * 40).astype('uint8'))
    num = np.max(labels)
    if visual:
        print('num in remove_inner_black', num)
    for i in range(num):
        domain = np.where(labels == i + 1, 1, 0)
        if visual:
            cv2.imshow('domain in remove_inner_black', (domain.astype('uint8')) * 255)
        rmin, rmax, cmin, cmax = mask2bbox(domain)
        if not (rmin == 0 or rmax == h - 1 or cmin == 0 or cmax == w - 1):
            labels = np.where(labels == i + 1, 0, labels)
    mask = np.where(labels != 0, mask, 0)
    mask = 255 - mask
    if visual:
        cv2.imshow('mask in remove_inner_black', mask)
    return mask

def mask2bbox(mask):#寻找二值化图像的mask=1处的方框
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    return rmin, rmax, cmin, cmax

class SuctionDataset(Dataset):
    """The suction network dataset.
    """

    def __init__(self, root, sample_ratio, augment, background_subtract, num_channels, radius):
        """Initializes the dataset.

        Args:
            root: (str) Root directory path.                                #数据集位置
            sample_ratio: (float) The ratio of negative to positive         #样本正负比例
                labels.
            normalize: (bool) Whether to normalize the images by            #标准化
                subtracting the mean and dividing by the std deviation.
            augment: (bool) Whether to apply data augmentation.             #数据增强
        """
        self._root = root
        self._sample_ratio = sample_ratio
        self._augment = augment
        self._background_subtract = background_subtract
        self._num_channels = num_channels
        self._radius = radius

        # figure out how many data samples we have
        self._get_filenames()

        stats = pickle.load(open(os.path.join(Path(self._root).parent, "mean_std.p"), "rb"))
        if self._num_channels == 4:
            self._c_norm = transforms.Normalize(mean=stats[0][0] * 3, std=stats[0][1] * 3)
        else:
            self._c_norm = transforms.Normalize(mean=stats[0][0], std=stats[0][1])
        self._d_norm = transforms.Normalize(mean=stats[1][0], std=stats[1][1])
        self._transform = transforms.ToTensor()

    def __len__(self):
        return len(self._filenames)

    def _get_filenames(self):
        self._filenames = glob.glob(os.path.join(self._root, "*/"))
        self._filenames.sort(key=lambda x: int(x.split("/")[-2]))

    def _load_state(self, name):
        """Loads the raw state variables.
        """
        # load the list of suction points
        suction_points_init = np.loadtxt(os.path.join(name, "suction_points_init.txt"), ndmin=2)
        suction_points_final = np.loadtxt(os.path.join(name, "suction_points_final.txt"), ndmin=2)

        # round
        num_init = suction_points_init.shape[0]
        suction_points_init = np.round(suction_points_init)[num_init - 1 : num_init]
        suction_points_final = np.round(suction_points_final)

        # load heightmaps
        c_height_f = np.asarray(Image.open(os.path.join(name, "final_color_height.png")))
        d_height_f = np.asarray(Image.open(os.path.join(name, "final_depth_height.png")))
        c_height_i = np.asarray(Image.open(os.path.join(name, "init_color_height.png")))
        d_height_i = np.asarray(Image.open(os.path.join(name, "init_depth_height.png")))
        c_height_i = c_height_i
        # convert depth to meters
        # d_height_f = (d_height_f * 1e-3).astype("float32")
        # d_height_i = (d_height_i * 1e-3).astype("float32")

        # load correspondences
        corrs = np.array([1, 2])
        #corrs = np.load(os.path.join(name, "corrs.npy"))

        # fix weird npy behavior
        if corrs.ndim > 2:
            corrs = [corrs[0]]
        else:
            corrs = corrs.tolist()

        # load kit mask
        kit_mask = np.load(os.path.join(name, "curr_kit_plus_hole_mask.npy"))

        return (
            c_height_i,
            d_height_i,
            c_height_f,
            d_height_f,
            suction_points_init,
            suction_points_final,
            corrs,
            kit_mask,
        )

    def _split_heightmap(self, height, source):
        """Splits a heightmap into a source and target.

        For suction, we just need the target heightmap.
        """
        half = height.shape[1] // 2
        self._half = half
        height_t = height[:, :half].copy()
        height_s = height[:, half:].copy()
        if source:
            return height_s
        return height_t

    def _sample_negative(self, positives):
        """Randomly samples negative pixel indices.
        """
        max_val = self._H * self._W
        num_pos = len(positives)
        num_neg = int(num_pos * self._sample_ratio)
        if self._sample_ratio < 70:
            negative_indices = []
            while len(negative_indices) < num_neg:
                negative = np.random.randint(0, max_val)
                if negative not in positives:
                    negative_indices.append(negative)
        else:
            allowed = list(set(np.arange(0, max_val)) - set(list(positives)))
            np.random.shuffle(allowed)
            negative_indices = allowed[:num_neg]
        negative_indices = np.unravel_index(negative_indices, (self._H, self._W))
        return negative_indices

    def _sample_translation(self, corrz, angle, center=True):
        aff_1 = np.eye(3)
        if center:
            aff_1[:2, 2] = [-self._uc, -self._vc]
        else:
            aff_1[:2, 2] = [-self._H//2, -self._W//2]
        aff_2 = gen_rot_mtx_anticlockwise(-angle)
        aff_3 = np.eye(3, 3)        #返回一个3*#的对角线为1 的数组
        if center:
            aff_3[:2, 2] = [self._uc, self._vc]
        else:
            aff_3[:2, 2] = [self._H//2, self._W//2]
        affine = aff_3 @ aff_2 @ aff_1
        affine = affine[:2, :]
        corrs = []
        for corr in corrz:
            ones = np.ones((len(corr), 1))
            corrs.append((affine @ np.hstack((corr, ones)).T).T)
        max_vv = corrs[0][:, 1].max()
        max_vu = corrs[0][corrs[0][:, 1].argmax()][0]
        min_vv = corrs[0][:, 1].min()
        min_vu = corrs[0][corrs[0][:, 1].argmin()][0]
        max_uu = corrs[0][:, 0].max()
        max_uv = corrs[0][corrs[0][:, 0].argmax()][1]
        min_uu = corrs[0][:, 0].min()
        min_uv = corrs[0][corrs[0][:, 0].argmin()][1]
        for t in corrs[1:]:
            if t[:, 1].max() > max_vv:
                max_vv = t[:, 1].max()
                max_vu = t[t[:, 1].argmax()][0]
            if t[:, 1].min() < min_vv:
                min_vv = t[:, 1].min()
                min_vu = t[t[:, 1].argmin()][0]
            if t[:, 0].max() > max_uu:
                max_uu = t[:, 0].max()
                max_uv = t[t[:, 0].argmax()][1]
            if t[:, 0].min() < min_uu:
                min_uu = t[:, 0].min()
                min_uv = t[t[:, 0].argmin()][1]
        tu = np.random.uniform(-min_vv + 10, self._W - max_vv - 10)
        tv = np.random.uniform(-min_uu + 10, self._H - max_uu - 10)
        return tu, tv

   
    def __getitem__(self, idx):
        name = self._filenames[idx]

        # load state
        c_height_i, d_height_i, c_height_f, d_height_f, \
            pos_suction_i, pos_suction_f, all_corrs, kit_mask = self._load_state(name)

        # split heightmap into source and target
        c_height_f = self._split_heightmap(c_height_f, False)
        d_height_f = self._split_heightmap(d_height_f, False)
        c_height_i = self._split_heightmap(c_height_i, True)
        d_height_i = self._split_heightmap(d_height_i, True)

        self._H, self._W = c_height_f.shape[:2]

        # offset indices to adjust for splitting
        #print(pos_suction_i.shape)
        pos_suction_i[:, 1] = pos_suction_i[:, 1] - self._half
        
        kit_mask[:, 1] = kit_mask[:, 1] - self._half

        pos_f = []
        for pos in pos_suction_f:
            rr, cc = circle(pos[0], pos[1], self._radius)
            pos_f.append(np.vstack([rr, cc]).T)
        pos_suction_f = np.concatenate(pos_f)
        pos_i = []
        for pos in pos_suction_i:
            rr, cc = circle(pos[0], pos[1], self._radius)
            pos_i.append(np.vstack([rr, cc]).T)
        pos_suction_i = np.concatenate(pos_i)

        '''for corr in all_corrs:
            corr[:, 1] = corr[:, 1] - self._half'''

        self._uc = int((kit_mask[:, 0].max() + kit_mask[:, 0].min()) // 2)
        self._vc = int((kit_mask[:, 1].max() + kit_mask[:, 1].min()) // 2)
        shape = (self._W, self._H)
        if self._augment:
            angle = np.radians(np.random.uniform(0, 360))
            tu, tv = 0, 0 # self._sample_translation([kit_mask], angle)
            aff_1 = np.eye(3)
            aff_1[:2, 2] = [-self._vc, -self._uc]
            aff_2 = gen_rot_mtx_anticlockwise(angle)
            aff_2[:2, 2] = [tu, tv]
            aff_3 = np.eye(3, 3)
            aff_3[:2, 2] = [self._vc, self._uc]
            affine = aff_3 @ aff_2 @ aff_1
            affine = affine[:2, :]
            c_height_i = cv2.warpAffine(c_height_i, affine, shape, flags=cv2.INTER_NEAREST)
            d_height_i = cv2.warpAffine(d_height_i, affine, shape, flags=cv2.INTER_NEAREST)
            aff_1[:2, 2] = [-self._uc, -self._vc]
            aff_2 = gen_rot_mtx_anticlockwise(-angle)
            aff_2[:2, 2] = [tv, tu]
            aff_3[:2, 2] = [self._uc, self._vc]
            affine = aff_3 @ aff_2 @ aff_1
            affine = affine[:2, :]
            pos_suction_i = (affine @ np.hstack((pos_suction_i, np.ones((len(pos_suction_i), 1)))).T).T
            kit_mask = (affine @ np.hstack((kit_mask, np.ones((len(kit_mask), 1)))).T).T

            # augment obj heightmap
            angle = np.radians(np.random.uniform(0, 360))
            tu, tv = self._sample_translation([p[:, 2:4].copy() for p in all_corrs], angle, False)
            aff_1 = np.eye(3)
            aff_1[:2, 2] = [-self._W//2, -self._H//2]
            aff_2 = gen_rot_mtx_anticlockwise(angle)
            aff_2[:2, 2] = [tu, tv]
            aff_3 = np.eye(3, 3)
            aff_3[:2, 2] = [self._W//2, self._H//2]
            affine = aff_3 @ aff_2 @ aff_1
            affine = affine[:2, :]
            c_height_f = cv2.warpAffine(c_height_f, affine, shape, flags=cv2.INTER_NEAREST)
            d_height_f = cv2.warpAffine(d_height_f, affine, shape, flags=cv2.INTER_NEAREST)
            aff_1[:2, 2] = [-self._H//2, -self._W//2]
            aff_2 = gen_rot_mtx_anticlockwise(-angle)
            aff_2[:2, 2] = [tv, tu]
            aff_3[:2, 2] = [self._H//2, self._W//2]
            affine = aff_3 @ aff_2 @ aff_1
            affine = affine[:2, :]
            pos_suction_f = (affine @ np.hstack((pos_suction_f, np.ones((len(pos_suction_f), 1)))).T).T

        #背景消除原版：使用两个阈值分别割出盒子和物体
        if self._background_subtract is not None:
            # self._background_subtract = [0.04, 0.047]
            # 用深度图区分盒子和物体
            condition = np.logical_and(d_height_i >= self._background_subtract[0],d_height_i <= self._background_subtract[1])
            idxs = np.vstack(np.where(condition)).T
            mask = np.zeros_like(d_height_i)
            mask[idxs[:, 0], idxs[:, 1]] = 1
            mask = misc.largest_cc(mask)
            idxs = np.vstack(np.where(mask == 1)).T
            mask = np.zeros_like(d_height_i)
            # 生成不包含白边的mask
            mask[idxs[:, 0].min():idxs[:, 0].max(), idxs[:, 1].min():idxs[:, 1].max()] = 1
            # mask = np.zeros_like(d_height_i)
            # mask[idxs[:, 0], idxs[:, 1]] = 1
            # mask = misc.largest_cc(np.logical_not(mask))
            idxs = np.vstack(np.where(mask == 0)).T
            # 把用深度图选出的白边置为0
            c_height_i[idxs[:, 0], idxs[:, 1]] = 0
            d_height_i[idxs[:, 0], idxs[:, 1]] = 0
            # 终止位置图片同上
            condition = np.logical_and(d_height_f >= self._background_subtract[2],d_height_f <= self._background_subtract[3])
            idxs = np.vstack(np.where(condition)).T
            mask = np.zeros_like(d_height_f)
            mask[idxs[:, 0], idxs[:, 1]] = 1
            mask = misc.largest_cc(np.logical_not(mask))
            idxs = np.vstack(np.where(mask == 1)).T
            c_height_f[idxs[:, 0], idxs[:, 1]] = 0
            d_height_f[idxs[:, 0], idxs[:, 1]] = 0

        #背景消除第二版：使用四个阈值（分别是盒子和物体的上下限）来分割物体
        # if self._background_subtract is not None:
        #     # 用深度图区分割出盒子和和盒子外的所有物体
        #     #对于source：c_height_i和d_height_i
        #     condition = np.logical_and(d_height_i >= self._background_subtract[0], d_height_i <= self._background_subtract[1])
        #     idxs = np.vstack(np.where(condition)).T
        #     mask = np.zeros_like(d_height_i)
        #     mask[idxs[:, 0], idxs[:, 1]] = 1
        #     mask = misc.largest_cc(mask)
        #     idxs = np.vstack(np.where(mask == 1)).T
        #     mask = np.zeros_like(d_height_i)
        #     mask[idxs[:, 0].min():idxs[:, 0].max(), idxs[:, 1].min():idxs[:, 1].max()] = 1
        #     # mask = np.zeros_like(d_height_i)
        #     # mask[idxs[:, 0], idxs[:, 1]] = 1
        #     # mask = misc.largest_cc(np.logical_not(mask))
        #     idxs = np.vstack(np.where(mask == 0)).T
        #     c_height_i[idxs[:, 0], idxs[:, 1]] = 0
        #     d_height_i[idxs[:, 0], idxs[:, 1]] = 0

        #     #对于target：c_height_d和d_height_f
        #     condition = np.logical_and(d_height_f >= self._background_subtract[2], d_height_f <= self._background_subtract[3])
        #     idxs = np.vstack(np.where(condition)).T
        #     mask = np.zeros_like(d_height_f)
        #     mask[idxs[:, 0], idxs[:, 1]] = 1
        #     mask = misc.largest_cc(np.logical_not(mask))
        #     idxs = np.vstack(np.where(mask == 1)).T
        #     c_height_f[idxs[:, 0], idxs[:, 1]] = 0
        #     d_height_f[idxs[:, 0], idxs[:, 1]] = 0

        #背景消除第三版：使用自适应阈值分割，参考图像是深度图,最后修改时间1105 16:09
        # if self._background_subtract :#TODO:修改条件
            
        #     thre_otsu, img_otsu = cv2.threshold(d_height_i,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
        #     bool_otsu = misc.largest_cc(img_otsu)#largest_cc返回值是一个bool类型的矩阵
        #     mask = np.zeros_like(d_height_i)
        #     # cv2.imshow("otsu",(bool_otsu*255).astype('uint8'))
        #     rmin,rmax,cmin,cmax = misc.mask2bbox(bool_otsu)
        #     mask[rmin:rmax,cmin:cmax] = 1
        #     c_height_i = np.where(mask, c_height_i, 0)
        #     d_height_i = np.where(mask, d_height_i, 0)
        #     # cv2.imshow('d_height_i', d_height_i)
        #     # cv2.imshow('c_height_i', c_height_i)
           
        #     seg_img_obj = cv2.adaptiveThreshold(d_height_f, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV,401, 2)
        #     # seg_img_obj = remove_inner_black(seg_img_objs)
        #     # cv2.imshow('adap_gaus_inv',adap_thre_gaus_inv)
        #     seg_img_obj = 255 - seg_img_obj
        #     seg_img_obj = remove_small_area(seg_img_obj,700)
        #     # seg_img_obj = remove_surrounding_white(seg_img_obj)
        #     #本来是黑部分就设为黑色，然后把白色的桌面设成0
        #     # mask = np.logical_or(bg_mask,desk_mask)
        #     assert mask.shape == d_height_f.shape
        #     c_height_f = np.where(mask,0,c_height_f)
        #     d_height_f = np.where(mask,0,d_height_f)

        # convert depth to meters
        d_height_f = (d_height_f * 1e-3).astype("float32")
        d_height_i = (d_height_i * 1e-3).astype("float32")

        if self._num_channels == 2:
            c_height_i = c_height_i[..., np.newaxis]
            c_height_f = c_height_f[..., np.newaxis]
        else:  # clone the gray channel 3 times
            c_height_i = np.repeat(c_height_i[..., np.newaxis], 3, axis=-1)
            c_height_f = np.repeat(c_height_f[..., np.newaxis], 3, axis=-1)

        # convert heightmaps tensors
        c_height_i = self._c_norm(self._transform(c_height_i))
        c_height_f = self._c_norm(self._transform(c_height_f))
        d_height_i = self._d_norm(self._transform(d_height_i[..., np.newaxis]))
        d_height_f = self._d_norm(self._transform(d_height_f[..., np.newaxis]))

        # concatenate height and depth into a 4-channel tensor
        img_tensor_i = torch.cat([c_height_i, d_height_i], dim=0)
        img_tensor_f = torch.cat([c_height_f, d_height_f], dim=0)
        img_tensor = torch.stack([img_tensor_i, img_tensor_f], dim=0)

        # add columns of 1 (positive labels)
        pos_label_i = np.hstack((pos_suction_i, np.ones((len(pos_suction_i), 1))))
        pos_label_f = np.hstack((pos_suction_f, np.ones((len(pos_suction_f), 1))))

        # generate negative labels
        neg_suction_i = np.vstack(self._sample_negative(pos_label_i)).T
        neg_label_i = np.hstack((neg_suction_i, np.zeros((len(neg_suction_i), 1))))
        neg_suction_f = np.vstack(self._sample_negative(pos_label_f)).T
        neg_label_f = np.hstack((neg_suction_f, np.zeros((len(neg_suction_f), 1))))

        # stack positive and negative into a single array
        label_i = np.vstack((pos_label_i, neg_label_i))
        label_f = np.vstack((pos_label_f, neg_label_f))

        # convert suction points to tensors
        label_tensor_i = torch.LongTensor(label_i)
        label_tensor_f = torch.LongTensor(label_f)
        label_tensor = [label_tensor_i, label_tensor_f]

        return img_tensor, label_tensor


def get_suction_loader(
    foldername,
    dtype="train",
    batch_size=1,
    sample_ratio=1,
    shuffle=True,
    augment=False,
    num_channels=2,
    background_subtract=None,
    radius=1,
    num_workers=4,
    use_cuda=True,
):
    """Returns a dataloader over the `Suction` dataset.

    Args:
        foldername: (str) The name of the folder containing the data.
        dtype: (str) Whether to use the train, validation or test partition.
        batch_size: (int) The number of data samples in a batch.
        sample_ratio: (float) The ratio of negative to positive
            labels.
        shuffle: (bool) Whether to shuffle the dataset at the end
            of every epoch.
        augment: (bool) Whether to apply data augmentation.
        num_workers: (int) How many processes to use. Each workers
            is responsible for loading a batch.
        use_cuda: (bool) Whether to use the GPU.
    """

    def _collate_fn(batch):
        """A custom collate function.

        This is to support variable length suction labels.
        """
        imgs = [b[0] for b in batch]
        labels = [b[1] for b in batch]
        imgs = torch.cat(imgs, dim=0)
        labels = [l for sublist in labels for l in sublist]
        return [imgs, labels]

    num_workers = min(num_workers, multiprocessing.cpu_count())
    root = os.path.join(config.ml_data_dir, foldername, dtype)

    dataset = SuctionDataset(
        root,
        sample_ratio,
        augment,
        background_subtract,
        num_channels,
        radius,
    )

    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        collate_fn=_collate_fn,
        pin_memory=True,
        num_workers=num_workers,
    )

    return loader
