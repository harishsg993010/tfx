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
"""Tests for Graph Partitioning."""

import os
import tempfile
import tensorflow as tf

from tensorflow.core.framework import graph_pb2

from create_complex_graph import save_examples_as_graphdefs
from graph_partition import _RemoteOpLayers
from graph_partition import get_graph_name_to_graph_def, partition_all_graphs


class RemoteOpLayerTest(tf.test.TestCase):
  """A test for the class _RemoteOpLayer"""
  def test_layers(self):
    """Validates the class through an example."""
    remote_op_relations = {'a1': [], 'a2': [], 'b1': ['a1'],
                           'b2': ['a1', 'a2'], 'c1': ['b1'],
                           'c2': ['b1', 'a1', 'b2', 'a2']}
    desired_outputs = [['a1', 'a2'], ['b1', 'b2'], ['c1', 'c2']]

    order = _RemoteOpLayers(remote_op_relations)
    self.assertEqual(desired_outputs, list(order))


class PartitionTest(tf.test.TestCase):
  """A set of tests for the graph partitioning library."""

  def setUp(self):
    """Sets up some example graphs and their partitions."""
    super().setUp()
    with tempfile.TemporaryDirectory() as temp_dir:
      # Save examples into a temporary directory
      save_examples_as_graphdefs(temp_dir)

      graph_name_to_filepath = {
          'main': os.path.join(temp_dir, 'main_graph.pb'),
          'remote_op_a': os.path.join(temp_dir, 'graph_a.pb'),
          'remote_op_b': os.path.join(temp_dir, 'graph_b.pb')}
      graph_name_to_outputs = {
          'main': ['AddN_1'],
          'remote_op_b': ['Add_1'],
          'remote_op_a': ['embedding_lookup/Identity']}

      graph_name_to_graph_def = get_graph_name_to_graph_def(
          graph_name_to_filepath)
      self.graph_name_to_specs = partition_all_graphs(
          graph_name_to_graph_def, graph_name_to_outputs)


  def test_subgraph_import_validity(self):
    """Tests if the partitioned subgraphs can be imported."""
    for execution_specs in self.graph_name_to_specs.values():
      for execution_spec in execution_specs:
        if execution_spec.is_remote_op:
          continue

        graph = tf.Graph()
        with graph.as_default():
          tf.import_graph_def(execution_spec.subgraph)


  def test_remote_op_specs(self):
    """Validates a remote op spec."""
    for execution_specs in self.graph_name_to_specs.values():
      for spec in execution_specs:
        if not spec.is_remote_op:
          continue

        self.assertIsNone(spec.subgraph)
        self.assertLen(spec.output_names, 1)


  def test_subgraphs_with_golden_set(self):
    """Checks if the partitioned subgraphs match the golden set."""
    for graph_name, specs in self.graph_name_to_specs.items():
      for spec in specs:
        if spec.is_remote_op:
          continue
        golden_graph_def = _get_golden_subgraph(graph_name, spec)
        self.assertEqual(golden_graph_def, spec.subgraph)


def _get_golden_subgraph(graph_name, spec):
  """Retrieves a corresponding golden subgraph."""
  filename = _generate_unique_filename(spec.input_names)
  filepath = os.path.join('testdata', graph_name, filename)

  graph_def = graph_pb2.GraphDef()
  with tf.io.gfile.GFile(filepath, 'rb') as f:
    graph_def.ParseFromString(f.read())
  return graph_def


def _generate_unique_filename(input_names):
  return "input_names-%s.pb" % ('-'.join(sorted(input_names)))


if __name__ == '__main__':
  tf.test.main()
  