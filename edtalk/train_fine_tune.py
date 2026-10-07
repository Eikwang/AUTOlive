import os

# 抑制 TensorFlow / oneDNN / absl 启动日志：被 torchvision 等依赖间接导入，
# 会在训练过程中反复打印 oneDNN port.cc 警告，污染进度输出。
# 必须在任何第三方 import (torch/torchvision) 之前设置，否则 TF C++ 层已初始化，日志已发出。
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')      # 仅保留 FATAL，屏蔽 INFO/WARN
os.environ.setdefault('TF_ENABLE_ONEDNN_OPTS', '0')     # 关闭 oneDNN 自定义算子提示

import argparse
import platform
import multiprocessing
import sys

# Windows 多进程: 必须使用 spawn 方式
if platform.system() == 'Windows':
    multiprocessing.set_start_method('spawn', force=True)

import torch
from torch.utils import data
from fine_tune.dataset import Finetune256
import torchvision
import torchvision.transforms as transforms
from fine_tune.trainer_fine_tune import Trainer
from torch.utils.tensorboard import SummaryWriter
import torch.distributed as dist
import torch.multiprocessing as mp
import torch.nn.functional as F
from torchvision import utils

torch.backends.cudnn.enabled = True
# cudnn.benchmark=True 会在首次前向时自动 benchmark 多个 kernel 选最快的,
# 但在 Windows + RTX 40 系 (Ada Lovelace, sm_89) + CUDA 12.8 下, 某些候选
# kernel 存在 bug, 触发 access violation (0xC0000005) 静默崩溃。
# 关闭 benchmark 使用 cudnn 默认的稳定 kernel, 速度差异很小 (<5%) 但更可靠。
torch.backends.cudnn.benchmark = False

# Windows WDDM 防护: 限制 CUDA 内存分配器, 减少虚拟内存碎片
if platform.system() == 'Windows':
    os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'garbage_collection_threshold:0.6'


def data_sampler(dataset, shuffle):
    if shuffle:
        return data.RandomSampler(dataset)
    else:
        return data.SequentialSampler(dataset)


def sample_data(loader):
    while True:
        for batch in loader:
            yield batch


def write_loss(i, vgg_loss, l1_loss, g_loss, d_loss, crop_loss, writer):
    writer.add_scalar('vgg_loss', vgg_loss.item(), i)
    writer.add_scalar('l1_loss', l1_loss.item(), i)
    writer.add_scalar('gen_loss', g_loss.item(), i)
    writer.add_scalar('dis_loss', d_loss.item(), i)
    writer.add_scalar('crop_loss', crop_loss.item(), i)
    writer.flush()


def ddp_setup(args, rank, world_size):
    os.environ['MASTER_ADDR'] = args.addr
    os.environ['MASTER_PORT'] = args.port
    dist.init_process_group("nccl", rank=rank, world_size=world_size)


def main(rank, world_size, args):
    if args.distributed:
        ddp_setup(args, rank, world_size)
        torch.cuda.set_device(rank)
    device = torch.device("cuda")

    # make logging folder
    log_path = os.path.join(args.exp_path, args.exp_name + '/log')
    checkpoint_path = os.path.join(args.exp_path, args.exp_name + '/checkpoint')
    os.makedirs(log_path, exist_ok=True)
    os.makedirs(checkpoint_path, exist_ok=True)
    writer = SummaryWriter(log_path)

    print('==> preparing dataset')
    # 解析数据集目录: 在 datapath 下寻找 count 子目录
    count_dir = os.path.join(args.datapath, 'count')
    if not os.path.isdir(count_dir):
        count_dir = args.datapath
    print(f'  Using data directory: {count_dir}')

    transform = torchvision.transforms.Compose([
        transforms.Resize((args.size, args.size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))]
    )

    dataset = Finetune256(count_dir, transform=transform,
                          facemark_dir=args.facemark_dir,
                          held_out_ratio=args.held_out_ratio,
                          mouth_weight=args.mouth_weight,
                          cross_video_only=args.cross_video_only,
                          open_mouth_ratio=args.open_mouth_ratio,
                          open_mouth_percent=args.open_mouth_percent,
                          img_size=args.size)

    if args.distributed:
        loader = data.DataLoader(
            dataset,
            num_workers=4,
            batch_size=args.batch_size // world_size,
            sampler=data.distributed.DistributedSampler(dataset, num_replicas=world_size, rank=rank, shuffle=True),
            pin_memory=True,
            drop_last=True,
        )
    else:
        loader = data.DataLoader(
            dataset,
            num_workers=args.num_workers,
            batch_size=args.batch_size,
            shuffle=True,
            pin_memory=True if args.num_workers > 0 else False,
            drop_last=True,
        )

    loader = sample_data(loader)

    print('==> initializing trainer')
    trainer = Trainer(args, device, rank)

    # resume
    if args.resume_ckpt is not None:
        args.start_iter = trainer.resume(args.resume_ckpt)
        print('==> resume from iteration %d' % (args.start_iter))

    # WDDM 防护间隔: 每 N 步 synchronize + empty_cache, 让 WDDM 驱动回收资源
    # Windows 下 WDDM 驱动虚拟内存映射会逐步退化, 导致 C 级 access violation
    # 定期 synchronize 确保 GPU 完成 + empty_cache 释放碎片化显存
    wddm_sync_interval = args.wddm_sync_interval

    print('==> training', flush=True)
    pbar = range(args.iter)
    last_saved_iter = args.start_iter

    try:
        for idx in pbar:
            i = idx + args.start_iter

            # loading data
            img_source, img_target, weight_map, mouth_bbox = next(loader)
            img_source = img_source.to(device, non_blocking=True)
            img_target = img_target.to(device, non_blocking=True)
            weight_map = weight_map.to(device, non_blocking=True)
            mouth_bbox = mouth_bbox.to(device, non_blocking=True)

            # update generator
            vgg_loss, l1_loss, gan_g_loss, img_recon, crop_loss = \
                trainer.gen_update(img_source, img_target, weight_map, mouth_bbox)

            # update discriminator
            gan_d_loss = trainer.dis_update(img_target, img_recon)

            # detach recon (free computation graph before display/val)
            img_recon = img_recon.detach()

            # WDDM 防护: 定期 synchronize + empty_cache 防止驱动状态退化
            if wddm_sync_interval > 0 and (idx + 1) % wddm_sync_interval == 0:
                torch.cuda.synchronize()
                torch.cuda.empty_cache()

            # write to log
            if not args.distributed or rank == 0:
                write_loss(idx, vgg_loss, l1_loss, gan_g_loss, gan_d_loss,
                           crop_loss, writer)

            # display
            if i % args.display_freq == 0 and (not args.distributed or rank == 0):
                mem_alloc = torch.cuda.memory_allocated() / 1024**3
                mem_reserved = torch.cuda.memory_reserved() / 1024**3
                print("[Iter %d/%d] [vgg: %f] [l1: %f] [g: %f] [d: %f] [c: %f] [mem: alloc=%.2fGB reserved=%.2fGB]"
                      % (i, args.iter, vgg_loss.item(), l1_loss.item(), gan_g_loss.item(), gan_d_loss.item(),
                         crop_loss.item(), mem_alloc, mem_reserved), flush=True)

                # display 样本图: 从训练集随机抽样（覆盖所有视频）
                try:
                    with torch.no_grad():
                        img_test_source, img_test_target, _w1, _bb = next(loader)
                        img_test_source = img_test_source.to(device, non_blocking=True)
                        img_test_target = img_test_target.to(device, non_blocking=True)

                        img_recon_test = trainer.sample(img_test_source, img_test_target)

                        # 交错排列: 每行 [Source | Target | Recon] 一个样本，方便对比
                        B = img_test_source.size(0)
                        interleaved = torch.stack(
                            [img_test_source, img_test_target, img_recon_test], dim=1
                        ).reshape(B * 3, *img_test_source.shape[1:])
                        sample = F.interpolate(interleaved, 256)
                        utils.save_image(
                            sample,
                            os.path.join(checkpoint_path, "step_%05d.jpg" % (i)),
                            nrow=3,
                            normalize=True,
                            value_range=(-1, 1),
                        )
                        del img_test_source, img_test_target, img_recon_test, interleaved, sample
                    torch.cuda.synchronize()
                except Exception as e:
                    print("[WARN] display failed at iter %d: %s" % (i, str(e)), flush=True)

            # save model
            if i % args.save_freq == 0 and (not args.distributed or rank == 0):
                torch.cuda.synchronize()
                trainer.save(i, checkpoint_path)
                last_saved_iter = i

    except KeyboardInterrupt:
        print("\n==> Training interrupted by user at iter %d" % (idx + args.start_iter), flush=True)
    except Exception as e:
        print("\n==> Training crashed at iter %d: %s" % (idx + args.start_iter, str(e)), flush=True)
        import traceback
        traceback.print_exc()
    finally:
        # 崩溃/中断时保存 checkpoint (不丢失训练进度)
        current_iter = (idx + args.start_iter) if 'idx' in dir() else args.start_iter
        if (not args.distributed or rank == 0) and current_iter > last_saved_iter:
            try:
                torch.cuda.synchronize()
                trainer.save(current_iter, checkpoint_path)
                print("==> Emergency checkpoint saved at iter %d." % current_iter, flush=True)
            except Exception as save_err:
                print("[ERROR] Failed to save emergency checkpoint: %s" % str(save_err), flush=True)

    return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iter", type=int, default=800000)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--batch_size", type=int, default=8,
                        help='批大小 (默认 4; 实测 batch=8 含 VGG 峰值 15.05GB/16GB 已在 '
                             'WDDM 溢出悬崖上 — 3.2x 耗时阶跃即溢出信号; batch=4 为 12.03GB 安全区)')
    parser.add_argument("--only_fine_tune_dec", action='store_true',
                        help='仅微调 Decoder (冻结 Encoder+Direction+fc)')
    parser.add_argument("--freeze_direction", action='store_true',
                        help='冻结 Direction+fc, 训练 Encoder+Decoder (仅全量微调时生效)')
    parser.add_argument("--d_reg_every", type=int, default=16)
    parser.add_argument("--g_reg_every", type=int, default=4)
    parser.add_argument("--resume_ckpt", type=str, default='ckpts/EDTalk_lip_pose.pt')
    parser.add_argument("--datapath", type=str, required=True,
                        help='Dataset directory (must contain count/ subdirectory with frame images)')
    parser.add_argument("--lr", type=float, default=0.001,
                        help='学习率 (默认 0.0001; 底模训练用 0.002, 微调取其 1/20 '
                             '减少对预训练权重的扰动)')
    parser.add_argument("--start_iter", type=int, default=0)
    parser.add_argument("--display_freq", type=int, default=1000)
    parser.add_argument("--save_freq", type=int, default=5000,
                        help='检查点保存间隔 (步), 建议设为 2000-5000 以减少崩溃丢失')
    parser.add_argument("--wddm_sync_interval", type=int, default=500,
                        help='WDDM 防护: 每 N 步 synchronize+empty_cache (0=禁用, Windows 建议 100)')
    parser.add_argument("--exp_path", type=str, default='ckpt_models',
                        help='实验根目录 (保存 checkpoint 和 log; 默认 ckpt_models 即项目根目录)')
    parser.add_argument("--exp_name", type=str, default='Obama')
    parser.add_argument("--addr", type=str, default='localhost')
    parser.add_argument("--port", type=str, default='12345')
    parser.add_argument("--num_workers", type=int, default=2,
                        help='DataLoader worker 进程数 (0=主线程加载)')
    parser.add_argument("--direction_lr_ratio", type=float, default=1.0,
                        help='Direction 模块学习率倍数 (仅全量微调且未冻结 Direction 时生效, '
                             '默认 1.0; 历史 5.0 会快速改写 Direction 语义, 破坏与 Audio2Lip '
                             '输出的接口一致性, 不建议调高)')
    parser.add_argument("--self_recon_weight", type=float, default=0.5,
                        help='自重建正则化权重 (默认 0.5, 防止 Decoder 跨身份训练时漂移; 0=禁用)')
    parser.add_argument("--cross_video_only", type=int, default=1,
                        help='跨视频采样开关 (默认 1: source/target 必须来自不同视频, '
                             '防单人数据集走捷径; 0=单视频回退)')
    parser.add_argument("--d_lr_ratio", type=float, default=0.1,
                        help='Discriminator 学习率倍率 (默认 0.1; 底模 D 对单人分布完胜导致 '
                             'G 对抗梯度消失→细节缺失, 弱化 D 恢复对抗张力, 目标 d_loss 0.1~1)')
    parser.add_argument("--held-out-ratio", type=float, default=0.05,
                        help='每视频末 N 比例帧从训练采样池剔除 (默认 0.05; '
                             '评估工具 mouth_metrics 的 --held_out_ratio 须与此对齐)')
    parser.add_argument("--mouth-weight", type=float, default=6.0,
                        help='嘴区损失权重 (默认 6.0, 精修阶段加权; 仅作用于 vgg/l1, '
                             'GAN 保持全图; 背景/脸部固定 1.0 — 归一化设计, 提高'
                             '嘴区权重会等比稀释脸部, 符合"脸部纹理可舍弃"取舍)')
    parser.add_argument("--open_mouth_ratio", type=float, default=0.5,
                        help='target 帧从张嘴池采样的概率 (默认 0.5; 牙齿细节只在'
                             '张嘴帧可见, 过采样张嘴帧=牙齿学习信号最直接杠杆)')
    parser.add_argument("--open_mouth_percent", type=float, default=0.25,
                        help='每视频 MAR top 百分比帧构成张嘴池 (默认 0.25; '
                             '有效张嘴帧采样权重≈ratio/percent=2x)')
    parser.add_argument("--crop_mouth_weight", type=float, default=1.0,
                        help='嘴区放大 crop 监督权重 (默认 1.0; VGG/L1 在 target 嘴区 '
                             'bbox 裁 128x128 crop 上计算 — 牙齿/嘴唇角色特征学习的'
                             '核心项, 特征分辨力比全图监督高 ~4 倍; 0=禁用)')
    parser.add_argument("--facemark-dir", type=str, default=None,
                        help='facemark npy 目录 (默认 datapath 同级 facemark/; 行序=帧数字序, 行数不符 fail-fast)')
    parser.add_argument("--ortho_weight", type=float, default=0.0,
                        help='Direction 正交正则化权重 (默认 0.0 = 底模训练行为; '
                             '底模 step2 无此项, 微调时不建议开启)')
    parser.add_argument("--gan_weight", type=float, default=1.0,
                        help='GAN 损失权重 (默认 1.0; 单人小数据集 D 过拟合劣化纹理时置 0 消融验证)')
    parser.add_argument("--beta1", type=float, default=0.0,
                        help='Adam beta1 (动量), 原始训练=0, 建议微调用 0 或 0.1 (默认 0.0)')
    opts = parser.parse_args()

    n_gpus = torch.cuda.device_count()

    if n_gpus >= 3:
        opts.distributed = True
        world_size = n_gpus
        print('==> training on %d gpus (distributed)' % n_gpus)
        mp.spawn(main, args=(world_size, opts,), nprocs=world_size, join=True)
    elif n_gpus == 1:
        opts.distributed = False
        print('==> training on 1 gpu (single)')
        main(0, 1, opts)
    else:
        raise RuntimeError("No GPU available")
