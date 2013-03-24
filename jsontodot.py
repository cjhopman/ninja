#!/usr/bin/env python

import json
import math
import os
import optparse
import re
import subprocess
import sys
from collections import deque

sys.path.append(os.path.join(os.environ.get('CONFIG_DIR'), 'third_party', 'pydot'))
import pydot


class DotGraph(object):
  def __init__(self, ninja_json, build_log):
    self.nodes = ninja_json['nodes']
    self.inputs = ninja_json['inputs']
    self.triggered_by = ninja_json['triggered_by']
    self.triggered = dict(((k, set()) for k in self.nodes))

    for k in self.triggered_by:
      self.triggered[self.triggered_by[k]].add(k)

    for k in self.nodes:
      inputs = self.inputs.setdefault(k, set())
      self.inputs[k] = set(inputs)
      self.nodes[k]['name'] = k

    self.outputs = dict(((k, set()) for k in self.nodes))
    for node in self.inputs:
      for node_input in self.inputs[node]:
        self.outputs[node_input].add(node)

    self.TopoSort()
    self.build_log = build_log.read()

    self.ParseBuildLogForApks()

  def FindNode(self, identifier):
    for k in self.nodes:
      node = self.nodes[k]
      if identifier in node['description']:
        return node


  def ParseBuildLogForApks(self):
    begin_re = re.compile('.*BEGIN TARGET.*{(.*)}.*')
    target_re = re.compile('.*Target (?P<name>[^:]*):.*finished.*\((?P<time>[0-9.]*)ms\).*')
    end_re = re.compile('.*BUILD SUCCESSFUL.*')

    node = None
    for line in self.build_log.split('\n'):
      if begin_re.match(line):
        name = begin_re.match(line).group(1)
        node = self.FindNode(name)
        print name, node
        node.setdefault('targets', [])

      target_match = target_re.match(line)

      if node and target_match:
        node.setdefault('targets', []).append({
          'name': target_match.group('name'),
          'time': target_match.group('time')
        })

      if node and end_re.match(line):
        if len(node['targets']) > 4:
          node['targets'] = filter(lambda n: int(n['time']) > 50, node['targets'])
        node = None




  def TopoSort(self):
    self.topo = list()
    num_inputs = {}
    for n in self.inputs:
      num_inputs[n] = len(self.inputs[n])

    while len(num_inputs) != 0:
      top = filter(lambda n: num_inputs[n] == 0, num_inputs)
      assert len(top) != 0

      self.topo += top
      for k in top:
        del num_inputs[k]
        for n in self.outputs[k]:
          num_inputs[n] -= 1


  def PrintNames(self, l):
    for n in l:
      print self.nodes[n]['description'],
    print ''


  def MergeTo(self, left, right):
    if left == right:
      return

    if left in self.triggered_by:
      left_triggered_by = self.triggered_by[left]
      self.triggered[left_triggered_by].remove(left)
      del self.triggered_by[left]

    left_triggered = self.triggered.setdefault(left, [])
    for node in left_triggered:
      self.triggered.setdefault(right, set()).add(node)
      self.triggered_by[node] = right
    del self.triggered[left]

    left_inputs = self.inputs[left]
    for node in left_inputs:
      self.inputs[right].add(node)
      self.outputs[node].add(right)
      self.outputs[node].remove(left)
    del self.inputs[left]

    left_outputs = self.outputs[left]
    for node in left_outputs:
      self.outputs[right].add(node)
      self.inputs[node].add(right)
      self.inputs[node].remove(left)
    del self.outputs[left]

    left_node = self.nodes[left]
    right_node = self.nodes[right]

    right_node['triggered'] = min(right_node['triggered'], left_node['triggered'])
    right_node['started'] = min(right_node['started'], left_node['started'])
    right_node['finished'] = max(right_node['finished'], left_node['finished'])
    if not 'merged' in right_node:
      right_node['description'] += ' +MORE!'
    right_node['merged'] = True

    del self.nodes[left]

  def MergeSiblings(self, prefixes):
    for node in self.topo:
      if not node in self.nodes:
        continue

      children = self.outputs[node]
      merge_to = dict()
      for child in list(children):
        for prefix in prefixes:
          if child in self.nodes and self.nodes[child]['description'].startswith(prefix):
            key = (prefix, self.triggered_by[child])
            if not key in merge_to:
              merge_to[key] = child
            else:
              self.MergeTo(child, merge_to[key])


  def MergeChildren(self, prefixes):
    for node in self.topo:
      if not node in self.nodes:
        continue

      children = self.outputs[node]
      for child in list(children):
        for prefix in prefixes:
          if child in self.nodes and self.nodes[child]['description'].startswith(prefix):
            self.MergeTo(child, node)


  def WriteDotFile(self, path):
    for k in self.nodes:
      node = self.nodes[k]
      node['taken'] = node['finished'] - node['triggered']

    graph = pydot.Dot()
    graph.set_graph_defaults(cluster=True)
    graph.set_compound(True)
    graph_nodes = {}
    def Name(node):
      return '_' + node

    def NameBottom(node):
      #if 'targets' in self.nodes[node]:
        #return Name(self.nodes[node]['targets'][-1]['name'])
      return Name(node)

    def NameTop(node):
      #if 'targets' in self.nodes[node]:
        #return Name(self.nodes[node]['targets'][0]['name'])
      return Name(node)

    def AdjustForClusters(edge, left, right):
      #if 'targets' in self.nodes[left]:
        #gedge.set_ltail('cluster_' + Name(left))
      #if 'targets' in self.nodes[right]:
        #gedge.set_lhead('cluster_' + Name(right))
      pass


    graph.set_node_defaults(shape='plaintext')

    longest_time = reduce(lambda t, n: max(t, self.nodes[n]['taken']), self.nodes, 0)
    for k in self.nodes:
      node = self.nodes[k]

      if 'taken' in node:
        fontsize = int(14.0 * math.sqrt(1 + 100 * node['taken'] / longest_time))
      else:
        fontsize = 14

      label = '< <table cellborder="0" border="' + str(fontsize / 4) + '">'
      label += '<tr><td><font point-size="' + str(fontsize) + '">'
      if 'description' in node and node['description']:
        label += node['description']
      else:
        label += 'xxxx'
      label += '</font></td></tr>'

      if 'description' in node:
        label += '<tr><td><font point-size="' + str(fontsize) + '">'
        label += 'taken: ' + str(node['taken'])
        label += '</font></td></tr>'
        label += '<tr><td><font point-size="' + str(fontsize) + '">'
        label += str(node['triggered']) + '/' + str(node['started']) + '/' + str(node['finished']) + '\n'
        label += '</font></td></tr>'

      gnode = graph_nodes.setdefault(k, pydot.Node(name=Name(k)))
      graph.add_node(gnode)
      if 'targets' in node:
        gnode.set_shape('record')
        targets = node['targets']
        for target in targets:
          fontsize = int(14.0 * math.sqrt(1 + 100 * int(target['time']) / longest_time))
          label += '<tr><td><font point-size="' + str(fontsize) + '">'
          label += target['name'] + '\n time: ' + target['time']
          label += '</font></td></tr>'
          print label
      label += '</table> >'

      gnode.set_label(label)
      #gnode.set_fontsize(fontsize)
      #gnode.set_penwidth(fontsize / 4)



    input_edges = {}
    for left in self.triggered:
      for right in self.triggered[left]:
        if left in self.nodes and right in self.nodes:
          gedge = input_edges.setdefault((left, right), pydot.Edge(NameBottom(left), NameTop(right)))
          AdjustForClusters(gedge, left, right)
          gedge.set_penwidth(3)
          gedge.set_arrowsize(5)
          graph.add_edge(gedge)


    for left in self.inputs:
      for right in self.inputs[left]:
        if left in self.nodes and right in self.nodes:
          gedge = input_edges.setdefault((left, right), pydot.Edge(NameBottom(right), NameTop(left)))
          AdjustForClusters(gedge, right, left)
          gedge.set_color('grey')
          gedge.set_penwidth(0.3)
          gedge.set_arrowsize(0.5)
          graph.add_edge(gedge)

    graph.write(path)

  def WriteCriticalPath(self, path):
    critical_path = []
    node = { 'finished': -1 }
    for n in self.nodes:
      candidate = self.nodes[n]
      if candidate['finished'] > node['finished']:
        node = candidate
    while True:
      critical_path.append(node)
      if not node['name'] in self.triggered_by:
        break
      trigger = self.triggered_by[node['name']]
      node = self.nodes[trigger]

    def FormatTime(tm):
      s, ms = divmod(tm, 1000)
      m, s = divmod(s, 60)
      return '{:01d}:{:02d}.{:03d}'.format(m, s, ms)

    with open(path, 'w') as outfile:
      for node in reversed(critical_path):
        print >>outfile, FormatTime(node['finished']), FormatTime(node['taken']), node['description']
        if 'targets' in node:
          for target in node['targets']:
            print >>outfile, '--------', FormatTime(int(target['time'])), target['name']




def Main(argv):
  parser = optparse.OptionParser()
  parser.add_option('--ninja-json')
  parser.add_option('--build-log')
  parser.add_option('--output')
  parser.add_option('--critical')
  (options, _) = parser.parse_args(argv)
  with open(options.ninja_json, 'r') as json_file:
    with open(options.build_log, 'r') as build_log:
      dot = DotGraph(json.load(json_file), build_log)
  dot.MergeSiblings(['CXX', 'STAMP', 'COPY', 'RULE'])
  #dot.MergeChildren(['STAMP'])
  if options.output:
    dot.WriteDotFile(options.output)
  if options.critical:
    dot.WriteCriticalPath(options.critical)


if __name__ == '__main__':
  sys.exit(Main(sys.argv))
