# -*- coding: utf-8 -*-

# (C) Copyright 2020, 2021, 2022 IBM. All Rights Reserved.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""aihwkit example 16: Train a GAN using Linear Layers to generate MNIST characters.

This experiment creates a Generative Adversarial Network (GAN)
-https://arxiv.org/abs/1406.2661- that generates characters (numbers)
alla MNIST. The experiment uses analog layers to implement the
network. Also, while training it plots the different characters
generated by the generator neural network vs. those used to train the
discriminator neural network.  Also, for every epoch, it shows the
loss for the generator and discriminator.

"""
# pylint: disable=invalid-name

import os
import glob
from datetime import datetime

import torch
from torch import nn
from torch.utils.data import DataLoader

from torchvision import transforms
from torchvision.datasets import MNIST

from torchvision.utils import make_grid, save_image

import matplotlib.pyplot as plt
import matplotlib.image as plt_image
from matplotlib import animation

from aihwkit.nn import AnalogLinear, AnalogSequential
from aihwkit.optim import AnalogSGD
from aihwkit.simulator.presets import MixedPrecisionEcRamMOPreset
from aihwkit.simulator.rpu_base import cuda

# Select the device model to use in the training.

# There are a number of presets available in
# aihwkit.simulator.presets. This is will also determine the analog
# optimizer used (e.g. mixed precision or full analog update)

# As an example we use a mixed precision preset using an ECRAM device model
from aihwkit.simulator.configs import MappingParameter
mapping = MappingParameter(weight_scaling_omega=0.8)
RPU_CONFIG = MixedPrecisionEcRamMOPreset(mapping=mapping)

# Set your parameters
SEED = 1
N_EPOCHS = 200
Z_DIM = 64
DISPLAY_STEP = 500
BATCH_SIZE = 256
LR = 2e-2

# Check device
USE_CUDA = 0
if cuda.is_compiled():
    USE_CUDA = 1
DEVICE = torch.device('cuda' if USE_CUDA else 'cpu')

# Path where the datasets will be stored.
PATH_DATASET = os.path.join('data', 'DATASET')

# Path to store results
RESULTS = os.path.join(os.getcwd(), 'results', 'GAN')


def store_tensor_images(image_tensor, label, current_step, num_images=25, size=(1, 28, 28)):
    """Store images using a uniform grid.

    Given a tensor of images, number of images, and size per image, stores the
    images using a uniform grid.

    Args:
        image_tensor (Tensor): tensor of images
        label (str): text label
        current_step (int): current step number
        num_images (int): number of images
        size (Tuple): shape of images
    """
    image_unflat = image_tensor.detach().cpu().view(-1, *size)
    image_grid = make_grid(image_unflat[:num_images], nrow=5)
    save_image(image_grid, os.path.join(RESULTS, f'{label}_step_{current_step}.png'))


def show_animation_fake_images():
    """Display images using a matplotlib animation.

    Displays every image labeled as "fake_images_step_*.png" inside the
    results/GAN folder using a matplotlib animation.
    """
    fig = plt.figure(figsize=(8, 8))
    plt.axis('off')
    sorted_available_images = sorted(glob.glob(f'{RESULTS}/fake_*.png'),
                                     key=lambda s: int(s.split('_')[-1].split('.png')[0]))
    ims = [[plt.imshow(plt_image.imread(i))] for i in sorted_available_images]
    ani = animation.ArtistAnimation(fig, ims, interval=500, repeat_delay=1000, blit=True)
    animation_writer = animation.PillowWriter()

    ani.save(os.path.join(RESULTS, 'replay_fake_images_gan.gif'), writer=animation_writer)
    plt.show()


def get_generator_block(input_dim, output_dim):
    """Return a block of the generator's neural network given input and output
    dimensions.

    Args:
        input_dim (int): the dimension of the input vector, a scalar
        output_dim (int): the dimension of the output vector, a scalar

    Returns:
        n.Module: a generator neural network layer, with a linear transformation
            followed by a batch normalization and then a relu activation
    """
    return AnalogSequential(
        AnalogLinear(
            input_dim,
            output_dim,
            bias=True,
            rpu_config=RPU_CONFIG
        ),
        nn.BatchNorm1d(output_dim),
        nn.ReLU(inplace=True),
    )


class Generator(nn.Module):
    """Generator Class.

    Args:
        z_dim: the dimension of the noise vector, a scalar
        im_dim: the dimension of the images, fitted for the dataset used, a scalar
            (MNIST images are 28 x 28 = 784 so that is your default)
        hidden_dim: the inner dimension, a scalar
    """

    def __init__(self, z_dim=10, im_dim=784, hidden_dim=128):
        super().__init__()
        # Build the neural network.
        self.gen = AnalogSequential(
            get_generator_block(z_dim, hidden_dim),
            get_generator_block(hidden_dim, hidden_dim * 2),
            get_generator_block(hidden_dim * 2, hidden_dim * 4),
            get_generator_block(hidden_dim * 4, hidden_dim * 8),
            AnalogLinear(
                hidden_dim * 8,
                im_dim,
                bias=True,
                rpu_config=RPU_CONFIG
            ),
            nn.Sigmoid(),
        )

    def forward(self, noise):
        """Complete a forward pass of the generator.

        Given a noise tensor, returns generated images.

        Args:
            noise (Tensor): a noise tensor with dimensions (n_samples, z_dim)

        Returns:
            Tensor: the generated images.
        """
        return self.gen(noise)


def get_noise(n_samples, z_dim, device='cpu'):
    """Create noise vectors.

    Given the dimensions (n_samples, z_dim), creates a tensor of that shape
    filled with random numbers from the normal distribution.

    Args:
        n_samples (int): the number of samples to generate, a scalar
        z_dim (int): the dimension of the noise vector, a scalar
        device (device): the device type

    Returns:
        Tensor: random vector
    """
    # NOTE: To use this on GPU with device='cuda', make sure to pass the device
    # argument to the function you use to generate the noise.
    return torch.randn(n_samples, z_dim).to(device)


def get_discriminator_block(input_dim, output_dim):
    """Discriminator Block.

    Function for returning a neural network of the discriminator given input
    and output dimensions.

    Args:
        input_dim (int): the dimension of the input vector, a scalar
        output_dim (int): the dimension of the output vector, a scalar

    Returns:
        nn.Sequential: a discriminator neural network layer, with a linear transformation
            followed by an nn.LeakyReLU activation with negative slope of 0.2
            (https://pytorch.org/docs/master/generated/torch.nn.LeakyReLU.html)
    """
    return nn.Sequential(
        nn.Linear(input_dim, output_dim), nn.LeakyReLU(2e-1, inplace=True)
    )


class Discriminator(nn.Module):
    """Discriminator Class.

    Args:
        im_dim (int): the dimension of the images, fitted for the dataset used, a scalar
            (MNIST images are 28x28 = 784 so that is your default)
        hidden_dim (int): the inner dimension, a scalar
    """

    def __init__(self, im_dim=784, hidden_dim=128):
        super().__init__()
        self.disc = nn.Sequential(
            get_discriminator_block(im_dim, hidden_dim * 4),
            get_discriminator_block(hidden_dim * 4, hidden_dim * 2),
            get_discriminator_block(hidden_dim * 2, hidden_dim),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, image):
        """Complete a forward pass of the discriminator.

        Given an image tensor, returns a 1-dimension tensor representing fake/real.

        Args:
            image (Tensor): a flattened image tensor with dimension (im_dim)

        Returns:
            Tensor: a 1-dimension tensor representing fake/real
        """
        return self.disc(image)


def get_disc_loss(gen, disc, criterion, real, num_images, z_dim, device):
    """Return the loss of the discriminator given inputs.

    Args:

        gen (nn.Module): the generator model, which returns an image
            given z-dimensional noise
        disc (nn.Module): the discriminator model, which returns a
            single-dimensional prediction of real/fake
        criterion (nn.Module): the loss function, which should be used
            to compare the discriminator's predictions to the ground
            truth reality of the images (e.g. fake = 0, real = 1)
        real (Tensor): a batch of real images
        num_images (int): the number of images the generator should
            produce, which is also the length of the real images
        z_dim (int): the dimension of the noise vector, a scalar
        device (device): the device type

    Returns:
        Tensor: a torch scalar loss value for the current batch

    """
    noise_vector = get_noise(num_images, z_dim, device)
    generated_images = gen(noise_vector)
    test_fake_im = disc(generated_images.detach())
    test_fake_im_loss = criterion(test_fake_im, torch.zeros_like(test_fake_im))

    test_real_im = disc(real.detach())
    test_real_im_loss = criterion(test_real_im, torch.ones_like(test_real_im))

    disc_loss = (test_real_im_loss + test_fake_im_loss) / 2
    return disc_loss


def get_gen_loss(gen, disc, criterion, num_images, z_dim, device):
    """Return the loss of the generator given inputs.

    Args:

        gen (nn.Module): the generator model, which returns an image
            given z-dimensional noise
        disc (nn.Module): the discriminator model, which returns a
            single-dimensional prediction of real/fake
        criterion (nn.Module): the loss function, which should be used
            to compare the discriminator's predictions to the ground
            truth reality of the images (e.g. fake = 0, real = 1)
        num_images (int): the number of images the generator should
            produce, which is also the length of the real images
        z_dim (int): the dimension of the noise vector, a scalar
        device (device): the device type

    Returns:
        Tensor: a torch scalar loss value for the current batch

    """
    noise_vector = get_noise(num_images, z_dim, device)
    generated_images = gen(noise_vector)
    test_fake_im = disc(generated_images)
    gen_loss = criterion(test_fake_im, torch.ones_like(test_fake_im))

    return gen_loss


def training_loop(
    gen, disc, gen_opt, disc_opt, criterion, dataloader, n_epochs, display_step
):
    """Training loop.

    Args:
        gen (nn.Module): the generator model
        disc (nn.Module): the discriminator model
        gen_opt (Optimizer): analog model optimizer for the generator
        disc_opt (Optimizer): analog model optimizer for the discriminator
        criterion (nn.Module): criterion to compute loss
        dataloader (DataLoader): Data set to train and evaluate the models
        n_epochs (int): global parameter to define epochs number
        display_step (int): defines the period to display the training progress
    """
    # pylint: disable=too-many-locals
    cur_step = 0
    mean_generator_loss = 0
    mean_discriminator_loss = 0

    for _ in range(n_epochs):
        # Dataloader returns the batches
        for real, _ in dataloader:
            cur_batch_size = len(real)

            # Flatten the batch of real images from the dataset.
            real = real.view(cur_batch_size, -1).to(DEVICE)

            # Update discriminator.
            # Zero out the gradients before backpropagation.
            disc_opt.zero_grad()

            # Calculate discriminator loss.
            disc_loss = get_disc_loss(
                gen, disc, criterion, real, cur_batch_size, Z_DIM, DEVICE
            )

            # Update gradients.
            disc_loss.backward()

            # Update optimizer.
            disc_opt.step()

            gen_opt.zero_grad()

            gen_loss = get_gen_loss(gen, disc, criterion, cur_batch_size, Z_DIM, DEVICE)

            gen_loss.backward()

            gen_opt.step()

            # Keep track of the average discriminator loss.
            mean_discriminator_loss += disc_loss.item() / display_step

            # Keep track of the average generator loss.
            mean_generator_loss += gen_loss.item() / display_step

            # Visualization code.
            if cur_step % display_step == 0 and cur_step > 0:
                print(
                    f'{datetime.now().time().replace(microsecond=0)} --- '
                    f'Step {cur_step}: '
                    f'Generator loss: {mean_generator_loss}, '
                    f'discriminator loss: {mean_discriminator_loss}'
                )
                fake_noise = get_noise(cur_batch_size, Z_DIM, device=DEVICE)
                fake = gen(fake_noise)

                store_tensor_images(fake, 'fake_images', cur_step)
                # For the example we will store only the fake images generated
                # store_tensor_images(real, 'real_images', cur_step).

                mean_generator_loss = 0
                mean_discriminator_loss = 0
            cur_step += 1


def main():
    """Train a PyTorch GAN analog model to generate fake characters alla MNIST dataset."""
    # Make sure the directory where to save the results exist.
    # Results include examples of the fake images generated.
    os.makedirs(RESULTS, exist_ok=True)
    torch.manual_seed(SEED)

    # Load MNIST dataset as tensors.
    dataloader = DataLoader(
        MNIST(PATH_DATASET, download=True, transform=transforms.ToTensor()),
        batch_size=BATCH_SIZE,
        shuffle=True,
    )

    print(f'\n{datetime.now().time().replace(microsecond=0)} --- ' f'Started GAN Example')

    gen = Generator(Z_DIM).to(DEVICE)
    gen_opt = AnalogSGD(gen.parameters(), lr=LR)
    gen_opt.regroup_param_groups(gen)

    disc = Discriminator().to(DEVICE)
    disc_opt = AnalogSGD(disc.parameters(), lr=LR)
    disc_opt.regroup_param_groups(disc)

    print(RPU_CONFIG)
    print(gen)
    print(disc)

    criterion = nn.BCEWithLogitsLoss()

    training_loop(gen, disc, gen_opt, disc_opt, criterion, dataloader, N_EPOCHS, DISPLAY_STEP)
    show_animation_fake_images()

    print(f'{datetime.now().time().replace(microsecond=0)} --- ' f'Completed GAN Example')


if __name__ == '__main__':
    # Execute only if run as the entry point into the program.
    main()
