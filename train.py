import os
import time
import math
import torch
import argparse
import numpy as np
from audio import save_wav
from utils import mode, to_arr
from logger import Tacotron2Logger
from hparams import hparams as hps
from torch.utils.data import DataLoader
from dataset import ljdataset, ljcollate
from model.model import Tacotron2, Tacotron2Loss
np.random.seed(0)
torch.manual_seed(0)
torch.cuda.manual_seed(0)

def prepare_dataloaders(fdir):
	trainset = ljdataset(fdir)
	collate_fn = ljcollate(hps.n_frames_per_step)
	train_loader = DataLoader(trainset, num_workers = hps.n_workers, shuffle = True,
							  batch_size = hps.batch_size, pin_memory = True,
							  drop_last = True, collate_fn = collate_fn)
	return train_loader


def load_checkpoint(ckpt_pth, model, optimizer):
	ckpt_dict = torch.load(ckpt_pth)
	model.load_state_dict(ckpt_dict['model'])
	optimizer.load_state_dict(ckpt_dict['optimizer'])
	iteration = ckpt_dict['iteration']
	return model, optimizer, iteration


def save_checkpoint(model, optimizer, iteration, ckpt_pth):
	torch.save({'model': model.state_dict(),
				'optimizer': optimizer.state_dict(),
				'iteration': iteration}, ckpt_pth)


def train(args):
	# build model
	model = Tacotron2()
	mode(model)
	optimizer = torch.optim.Adam(model.parameters(), lr = hps.lr, weight_decay = hps.weight_decay)
	criterion = Tacotron2Loss()
	
	# load checkpoint
	iteration = 1
	if args.ckpt_pth != '':
		model, optimizer, iteration = load_checkpoint(args.ckpt_pth, model, optimizer)
		iteration += 1 # next iteration is iteration+1
	
	# get scheduler
	if hps.wu:
		lr_lambda = lambda step: hps.wu_step**0.5*np.minimum((step+1)*hps.wu_step**-1.5, (step+1)**-0.5)
		if args.ckpt_pth != '':
			scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda, last_epoch = iteration)
		else:
			scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
	
	# make dataset
	train_loader = prepare_dataloaders(args.data_dir)
	
	# get logger ready
	if args.log_dir != '' and not os.path.isdir(args.log_dir):
		os.makedirs(args.log_dir)
		os.chmod(args.log_dir, 0o775)
		logger = Tacotron2Logger(args.log_dir)

	# get ckpt_dir ready
	if args.ckpt_dir != '' and not os.path.isdir(args.ckpt_dir):
		os.makedirs(args.ckpt_dir)
		os.chmod(args.ckpt_dir, 0o775)
	
	model.train()
	# ================ MAIN TRAINNIG LOOP! ===================
	while iteration <= hps.max_iter:
		for batch in train_loader:
			if iteration > hps.max_iter:
				break
			start = time.perf_counter()
			x, y = model.parse_batch(batch)
			y_pred = model(x)

			# loss
			loss = criterion(y_pred, y)
			
			# zero grad
			model.zero_grad()
			
			# backward, grad_norm, and update
			loss.backward()
			grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), hps.grad_clip_thresh)
			optimizer.step()
			if hps.wu:
				scheduler.step()
			
			# info
			dur = time.perf_counter()-start
			print('Iter: {} Loss: {:.2e} Grad Norm: {:.2e} {:.1f}s/it'.format(
				iteration, loss.item(), grad_norm, dur))
			
			# log
			if args.log_dir != '' and (iteration % hps.iters_per_log == 0):
				logger.log_training(loss.item(), grad_norm, learning_rate, dur, iteration)
			
			# save ckpt
			if args.ckpt_dir != '' and (iteration % hps.iters_per_ckpt == 0):
				ckpt_pth = os.path.join(hps.ckpt_dir, 'ckpt_{}'.format(iteration))
				save_checkpoint(model, optimizer, iteration, ckpt_pth)

			iteration += 1


if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	# path
	parser.add_argument('-d', '--data_dir', type = str, default = 'data',
						help = 'directory to load data')
	parser.add_argument('-l', '--log_dir', type = str, default = 'log',
						help = 'directory to save tensorboard logs')
	parser.add_argument('-cd', '--ckpt_dir', type = str, default = 'ckpt',
						help = 'directory to save checkpoints')
	parser.add_argument('-cp', '--ckpt_pth', type = str, default = '',
						help = 'directory to load checkpoints')

	args = parser.parse_args()
	
	torch.backends.cudnn.enabled = True
	torch.backends.cudnn.benchmark = True
	train(args)