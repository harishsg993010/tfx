# Copyright 2020 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Modules for organizing various model server binaries."""

import abc
import os
from typing import Any, Dict, List, Optional

from docker import types as docker_types

from tfx.components.infra_validator.model_server_clients import base_client
from tfx.components.infra_validator.model_server_clients import tensorflow_serving_client
from tfx.proto import infra_validator_pb2
from tfx.utils.model_paths import tf_serving_flavor


def parse_serving_binaries(  # pylint: disable=invalid-name
    serving_spec: infra_validator_pb2.ServingSpec) -> List['ServingBinary']:
  """Parse `ServingBinary`s from `ServingSpec`."""
  result = []
  serving_binary = serving_spec.WhichOneof('serving_binary')
  if serving_binary == 'tensorflow_serving':
    config = serving_spec.tensorflow_serving
    image_name = config.image_name or None
    for tag in config.tags:
      result.append(TensorFlowServing(image_name=image_name,
                                      model_name=serving_spec.model_name,
                                      tag=tag))
    for digest in config.digests:
      result.append(TensorFlowServing(image_name=image_name,
                                      model_name=serving_spec.model_name,
                                      digest=digest))
    return result
  else:
    raise ValueError('Invalid serving_binary {}'.format(serving_binary))


class ServingBinary(abc.ABC):
  """Base class for serving binaries."""

  @property
  @abc.abstractmethod
  def container_port(self) -> int:
    """Container port of the model server.

    Only applies to docker compatible serving binaries.
    """
    raise NotImplementedError('{} is not docker compatible.'.format(
        type(self).__name__))

  @property
  @abc.abstractmethod
  def image(self) -> str:
    """Container image of the model server.

    Only applies to docker compatible serving binaries.
    """
    raise NotImplementedError('{} is not docker compatible.'.format(
        type(self).__name__))

  @abc.abstractmethod
  def MakeEnvVars(self, *args: Any) -> Dict[str, str]:
    """Construct environment variables to be used in container image.

    Only applies to docker compatible serving binaries.

    Args:
      *args: List of unresolved variables to configure environment variables.

    Returns:
      A dictionary of environment variables inside container.
    """
    raise NotImplementedError('{} is not docker compatible.'.format(
        type(self).__name__))

  @abc.abstractmethod
  def MakeDockerRunParams(self, *args: Any) -> Dict[str, str]:
    """Make parameters for docker `client.containers.run`.

    Only applies to docker compatible serving binaries.

    Args:
      *args: List of unresolved variables to configure docker run parameters.

    Returns:
      A dictionary of docker run parameters.
    """
    raise NotImplementedError('{} is not docker compatible.'.format(
        type(self).__name__))

  @abc.abstractmethod
  def MakeClient(self, endpoint: str) -> base_client.BaseModelServerClient:
    """Create a model server client of this serving binary."""
    raise NotImplementedError('{} does not implement MakeClient.'.format(
        type(self).__name__))


class TensorFlowServing(ServingBinary):
  """TensorFlow Serving binary."""

  _BASE_DOCKER_RUN_PARAMS = {
      # Enable auto-removal of the container on docker daemon after container
      # process exits.
      'auto_remove': True,
      # Run container in the background instead of streaming its output.
      'detach': True,
      # Publish all ports to the host.
      'publish_all_ports': True,
  }
  _DEFAULT_IMAGE_NAME = 'tensorflow/serving'
  _DEFAULT_GRPC_PORT = 8500
  _DEFAULT_MODEL_BASE_PATH = '/model'

  def __init__(
      self,
      model_name: str,
      image_name: Optional[str] = None,
      tag: Optional[str] = None,
      digest: Optional[str] = None,
  ):
    super().__init__()
    self._model_name = model_name
    if (tag is None) == (digest is None):
      raise ValueError('Exactly one of `tag` or `digest` should be used.')
    image_name = image_name or self._DEFAULT_IMAGE_NAME
    if tag is not None:
      self._image = '{}:{}'.format(image_name, tag)
    else:
      self._image = '{}@{}'.format(image_name, digest)

  @property
  def container_port(self) -> int:
    return self._DEFAULT_GRPC_PORT

  @property
  def image(self) -> str:
    return self._image

  def MakeEnvVars(self, model_path: Optional[str] = None) -> Dict[str, str]:
    if model_path is None:
      model_base_path = self._DEFAULT_MODEL_BASE_PATH
    else:
      model_base_path = tf_serving_flavor.parse_model_base_path(model_path)
    return {
        'MODEL_NAME': self._model_name,
        'MODEL_BASE_PATH': model_base_path,
        'TF_CPP_MAX_VLOG_LEVEL': '3',
    }

  def MakeDockerRunParams(self, model_path: str,
                          needs_mount: bool) -> Dict[str, Any]:
    """Make parameters for docker `client.containers.run`.

    Args:
      model_path: A path to the model.
      needs_mount: If True, model_path will be mounted to the container.

    Returns:
      A dictionary of docker run parameters.
    """
    result = dict(
        self._BASE_DOCKER_RUN_PARAMS,
        image=self._image)

    if needs_mount:
      # model_path should be a local directory. In order to make TF Serving see
      # the host model path, we need to mount model path volume to the
      # container.
      if not os.path.isdir(model_path):
        raise AssertionError('{} does not exist'.format(model_path))
      container_model_path = tf_serving_flavor.make_model_path(
          model_base_path=self._DEFAULT_MODEL_BASE_PATH,
          model_name=self._model_name,
          version=1)
      result.update(
          environment=self.MakeEnvVars(),
          mounts=[
              docker_types.Mount(
                  type='bind',
                  target=container_model_path,
                  source=model_path,
                  read_only=True)
          ])
    else:
      # model_path is presumably a remote URI. TF Serving is able to pickup
      # model in remote directly using gfile, so all we need to do is setting
      # environment variables correctly.
      result.update(
          environment=self.MakeEnvVars(model_path=model_path))

    return result

  def MakeClient(self, endpoint: str) -> base_client.BaseModelServerClient:
    return tensorflow_serving_client.TensorFlowServingClient(
        endpoint=endpoint, model_name=self._model_name)
