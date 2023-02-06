import argparse
import time

import torch
import numpy as np
from torchvision import transforms
from torchvision import datasets
from torch.utils.data import DataLoader
import torch.nn.functional as F
import torch.optim as optim

from matchnet.code.ml.dataloader import suction
from matchnet.code.ml.models.suction import SuctionNet
from matchnet.code.ml import losses

import warnings
warnings.filterwarnings("ignore")


# python form2fit/code/train_suction.py 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Form2Fit suction Module")
    parser.add_argument("--batchsize", type=int, default=4, help="The batchsize of the dataset.")
    parser.add_argument("--sample_ratio", type=int, default=3, help="The ratio of negative to positive labels.")
    parser.add_argument("--epochs", type=int, default=160, help="The number of training epochs.")
    parser.add_argument("--augment", action='store_true', help="(bool) Whether to apply data augmentation.")
    parser.add_argument("--background_subtract", action='store_true', help="Whether to apply background subtraction.")
    parser.add_argument("--dtype", type=str, default="valid")
    parser.add_argument("--imgsize", type=list, default=[848,480], help="size of final image.")
    parser.add_argument("--root", type=str, default="", help="the path of dataset")
    parser.add_argument("--savepath", type=str, default="form2fit/code/ml/savedmodel/", help="the path of saved models")
    opt = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    batch_size = opt.batchsize
    epochs = opt.epochs
    root = ""
    savepath = opt.savepath
    testroot = "dataset/test"
    background_subtract = opt.background_subtract
    if background_subtract:
        print("using background subtraction")
    num_channels = 2
    sample_ratio = 7
    radius = 10  # 判断正样本的半径

    print("--------------start preparing data--------------")
    
    
    train_loader = suction.get_suction_loader(root, 
                                        dtype="train", 
                                        batch_size=batch_size, 
                                        num_channels=num_channels, 
                                        sample_ratio=sample_ratio, 
                                        augment=True if opt.augment else False,
                                        shuffle=True,
                                        radius=radius,
                                        background_subtract=background_subtract)

    #test_loader = suction.get_suction_loader(testroot, dtype="train", batch_size= batch_size, sample_ratio=1)

    model = SuctionNet(num_channels=num_channels).to(device)
    criterion = losses.SuctionLoss(sample_ratio=sample_ratio, device=device)
    optimizer = torch.optim.Adam(model.parameters(),lr=1e-4)
    train_loss = []
    valid_loss = []
    train_epochs_loss = []
    valid_epochs_loss = []

    print("----------------start training-----------------")
    t0 = time.time()
    for epoch in range(epochs):
        model.train()
        train_epoch_loss = []

        for i, (imgs, labels) in enumerate(train_loader):
            imgs = imgs.to(device)
            
            for j in range(len(labels)):
                labels[j] = labels[j].cuda()
                
            output = model(imgs)

            optimizer.zero_grad()
            loss = criterion(output, labels)
            loss.backward()
            optimizer.step()
            train_epoch_loss.append(loss.item())
            train_loss.append(loss.item())

            #print("epoch={}/{},{}/{}of train, loss={}".format(epoch, opt.epochs, i, len(train_loader),loss.item()))
            if i % (len(train_loader)//2) == 0:
                print("epoch = {}/{}, {}/{} of train, loss = {}".format(epoch, opt.epochs, i, len(train_loader),loss.item()))
                train_epochs_loss.append(np.average(train_epoch_loss))

        if (epoch % 40 == 0 and epoch != 0) or (epoch < 155 and epoch > 145):                            # 140 - 160 精确选取，因为150epoch时达到最佳效果
            print("---------saving model for epoch {}----------".format(epoch))
            savedpath = savepath + 'suck_epoch' + str(epoch) + '.pth'
            torch.save(model.state_dict(), savedpath)

        if epoch + 1 == epochs:
            print("---------saving model for last epoch ----------")
            finalsavedpath = savepath + 'suck_final' + '.pth'
            torch.save(model.state_dict(), finalsavedpath)