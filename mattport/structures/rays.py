"""
Some ray datastructures.
"""
import random
from dataclasses import dataclass
from typing import Optional

import torch
from torchtyping import TensorType

from mattport.utils.misc import is_not_none


@dataclass
class PointSamples:
    """Samples in space."""

    positions: TensorType[..., 3] = None  # XYZ locations
    directions: TensorType[..., 3] = None  # Unit direction vector
    camera_indices: TensorType[..., 1] = None  # Camera index
    valid_mask: TensorType[..., 1] = None  # Rays that are valid


@dataclass
class RaySamples:
    """Samples along a ray"""

    positions: TensorType[..., 3] = None  # XYZ locations
    directions: TensorType[..., 3] = None  # Unit direction vector
    camera_indices: TensorType[..., 1] = None  # Camera index
    valid_mask: TensorType[..., 1] = None  # Rays that are valid
    ts: TensorType[..., 1] = None  # "time steps", distances along ray
    deltas: TensorType[..., 1] = None  # "width" of each sample

    def to_point_samples(self) -> PointSamples:
        """Convert to PointSamples instance and return."""
        # TODO: make this more interpretable
        return PointSamples(positions=self.positions, directions=self.directions, valid_mask=self.valid_mask)

    def get_weights(self, densities: TensorType[..., "num_samples", 1]) -> TensorType[..., "num_samples"]:
        """Return weights based on predicted densities

        Args:
            densities (TensorType[..., "num_samples", 1]): Predicted densities for samples along ray

        Returns:
            TensorType[..., "num_samples"]: Weights for each sample
        """

        delta_density = self.deltas * densities[..., 0]
        alphas = 1 - torch.exp(-delta_density)

        transmittance = torch.cumsum(delta_density[..., :-1], dim=-1)
        transmittance = torch.cat(
            [torch.zeros((*transmittance.shape[:1], 1)).to(densities.device), transmittance], axis=-1
        )
        transmittance = torch.exp(-transmittance)  # [..., "num_samples"]

        weights = alphas * transmittance  # [..., "num_samples"]

        return weights

    def set_valid_mask(self, valid_mask: TensorType[..., "num_samples"]) -> None:
        """Sets valid mask"""
        self.valid_mask = valid_mask


@dataclass
class RayBundle:
    """A bundle of ray parameters."""

    origins: TensorType["num_rays", 3]  # Ray origins
    directions: TensorType["num_rays", 3]  #
    camera_indices: Optional[TensorType["num_rays"]] = None
    nears: Optional[TensorType["num_rays"]] = None
    fars: Optional[TensorType["num_rays"]] = None
    valid_mask: Optional[TensorType["num_rays"]] = None

    def to_camera_ray_bundle(self, image_height, image_width) -> "CameraRayBundle":
        """Returns a CameraRayBundle from this object."""
        camera_ray_bundle = CameraRayBundle(
            origins=self.origins.view(image_height, image_width, 3),
            directions=self.directions.view(image_height, image_width, 3),
            camera_indices=self.camera_indices.view(image_height, image_width)
            if not isinstance(self.camera_indices, type(None))
            else None,
        )
        return camera_ray_bundle

    def move_to_device(self, device):
        """Move to a device."""
        self.origins = self.origins.to(device)
        self.directions = self.directions.to(device)
        if not isinstance(self.camera_indices, type(None)):
            self.camera_indices = self.camera_indices.to(device)

    def __len__(self):
        num_rays = self.origins.shape[0]
        return num_rays

    def sample(self, num_rays: int):
        """Returns a RayBundle as a subset of rays.

        Args:
            num_rays (int):

        Returns:
            RayBundle: _description_
        """
        assert num_rays <= len(self)
        indices = random.sample(range(len(self)), k=num_rays)
        return RayBundle(
            origins=self.origins[indices],
            directions=self.directions[indices],
            camera_indices=self.camera_indices[indices],
        )

    def get_masked_ray_bundle(self, valid_mask):
        """Return a masked instance of the ray bundle."""
        return RayBundle(
            origins=self.origins[valid_mask],
            directions=self.directions[valid_mask],
            camera_indices=self.camera_indices[valid_mask] if is_not_none(self.camera_indices) else None,
            nears=self.nears[valid_mask] if is_not_none(self.nears) else None,
            fars=self.fars[valid_mask] if is_not_none(self.fars) else None,
            valid_mask=self.valid_mask[valid_mask] if is_not_none(self.valid_mask) else None,
        )

    def get_ray_samples(self, ts: TensorType["num_rays", "num_samples"]) -> RaySamples:
        """
        Args:
            ts (TensorType["num_rays", "num_samples"]): _description_

        Returns:
            RaySamples: _description_
        """
        positions = self.origins[:, None] + ts[:, :, None] * self.directions[:, None]
        directions = self.directions.unsqueeze(1).repeat(1, positions.shape[1], 1)
        valid_mask = torch.ones_like(ts, dtype=torch.bool)

        dists = ts[..., 1:] - ts[..., :-1]
        dists = torch.cat([dists, dists[..., -1:]], -1)  # [N_rays, N_samples]
        deltas = dists * torch.norm(self.directions[..., None, :], dim=-1)

        if self.camera_indices is not None:
            camera_indices = self.camera_indices.unsqueeze(1).repeat(1, positions.shape[1])
        else:
            camera_indices = None

        ray_samples = RaySamples(
            positions=positions,
            directions=directions,
            camera_indices=camera_indices,
            valid_mask=valid_mask,
            ts=ts,
            deltas=deltas,
        )

        return ray_samples


@dataclass
class CameraRayBundle:
    """_summary_"""

    origins: TensorType["image_height", "image_width", 3]
    directions: TensorType["image_height", "image_width", 3]
    camera_indices: Optional[TensorType["image_height", "image_width", 3]] = None
    camera_index: int = None

    def __post_init__(self):
        if not isinstance(self.camera_index, type(None)):
            self.set_camera_indices(self.camera_index)

    def set_camera_indices(self, camera_index: int):
        """Sets the camera indices for a specific camera index."""
        self.camera_index = camera_index
        self.camera_indices = torch.ones_like(self.origins[..., 0]).long() * camera_index

    def get_num_rays(self):
        """Return the number of rays in this bundle."""
        image_height, image_width = self.origins.shape[:2]
        num_rays = image_height * image_width
        return num_rays

    def to_ray_bundle(self) -> RayBundle:
        """_summary_

        Returns:
            RayBundle: _description_
        """
        # TODO(ethan): handle camera_index
        ray_bundle = RayBundle(origins=self.origins.view(-1, 3), directions=self.directions.view(-1, 3))
        return ray_bundle

    def get_row_major_sliced_ray_bundle(self, start_idx, end_idx):
        """Return a RayBundle"""
        camera_indices = (
            self.camera_indices.view(-1)[start_idx:end_idx] if not isinstance(self.camera_indices, type(None)) else None
        )
        return RayBundle(
            origins=self.origins.view(-1, 3)[start_idx:end_idx],
            directions=self.directions.view(-1, 3)[start_idx:end_idx],
            camera_indices=camera_indices,
        )