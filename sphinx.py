import datetime
import os
import sys
import time

import numpy as np

from utils import *


class SphinxModel(object):
    default_points = ['neckline_left', 'neckline_right', 'center_front', 'shoulder_left', 'shoulder_right',
                      'armpit_left', 'armpit_right', 'waistline_left', 'waistline_right', 'cuff_left_in',
                      'cuff_left_out', 'cuff_right_in', 'cuff_right_out', 'top_hem_left', 'top_hem_right',
                      'waistband_left', 'waistband_right', 'hemline_left', 'hemline_right', 'crotch',
                      'bottom_left_in', 'bottom_left_out', 'bottom_right_in', 'bottom_right_out']

    def __init__(self, nFeats=512, nStacks=4, nLow=4, out_dim=24, batch_size=16, drop_rate=0.5,
                 learning_rate=1e-3, decay=0.96, decay_step=2000, dataset=None, training=True, w_summary=True,
                 logdir_train=None, logdir_test=None, w_loss=False, points=default_points, name='sphinx'):

        self.nStacks = nStacks
        self.nFeats = nFeats
        self.out_dim = out_dim
        self.batch_size = batch_size
        self.training = training
        self.dropout_rate = drop_rate
        self.learning_rate = learning_rate
        self.decay = decay
        self.name = name
        self.decay_step = decay_step
        self.nLow = nLow
        self.dataset = dataset
        self.cpu = '/cpu:0'
        self.gpu = '/gpu:0'
        self.logdir_train = logdir_train
        self.logdir_test = logdir_test
        self.points = points
        self.w_summary = w_summary
        self.w_loss = w_loss
        self.resume = {}

    def generate_model(self):
        start_time = time.time()

        print('CREATE MODEL:')
        with tf.device(self.gpu):
            with tf.name_scope('inputs'):
                self.img = tf.placeholder(dtype=tf.float32, shape=(None, 256, 256, 3), name='input')
                if self.w_loss:
                    self.weights = tf.placeholder(dtype=tf.float32, shape=(None, self.out_dim))
                self.gt_maps = tf.placeholder(dtype=tf.float32, shape=(None, self.nStacks, 64, 64, self.out_dim))
            print('---Inputs : Done.')
            self.output = self._graph_sphinx()
            print('---Graph : Done.')
            with tf.name_scope('loss'):
                if self.w_loss:
                    self.loss = tf.reduce_mean(self.weighted_bce_loss(), name='reduced_loss')
                else:
                    self.loss = tf.reduce_mean(
                        tf.nn.sigmoid_cross_entropy_with_logits(logits=self.output, labels=self.gt_maps),
                        name='cross_entropy_loss'
                    )
            print('---Loss : Done.')

        with tf.device(self.cpu):
            with tf.name_scope('error'):
                self._error_computation()
            print('---Error : Done.')
            with tf.name_scope('steps'):
                self.train_step = tf.Variable(0, name='global_step', trainable=False)
            with tf.name_scope('lr'):
                lr = tf.train.exponential_decay(
                    self.learning_rate, self.train_step, self.decay_step, self.decay,
                    staircase=True, name='learning_rate'
                )
            print('---Learning Rate : Done.')

        with tf.device(self.gpu):
            with tf.name_scope('rmsprop'):
                rmsprop = tf.train.RMSPropOptimizer(learning_rate=lr)
            print('---Optimizer : Done.')
            with tf.name_scope('minimizer'):
                update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                with tf.control_dependencies(update_ops):
                    self.train_rmsprop = rmsprop.minimize(self.loss, self.train_step)
            print('---Minimizer : Done.')
        self.init = tf.global_variables_initializer()
        print('---Init : Done.')

        with tf.device(self.cpu):
            with tf.name_scope('training'):
                tf.summary.scalar('loss', self.loss, collections=['train'])
                tf.summary.scalar('learning_rate', lr, collections=['train'])
            with tf.name_scope('summary'):
                for i in range(len(self.points)):
                    tf.summary.scalar(self.points[i], self.point_error[i], collections=['train', 'test'])

        self.train_op = tf.summary.merge_all('train')
        self.test_op = tf.summary.merge_all('test')
        self.weight_op = tf.summary.merge_all('weight')

        end_time = time.time()
        print('Model created (' + str(int(abs(end_time - start_time))) + ' sec.)')
        del start_time, end_time

    def _train(self, nEpochs=10, epoch_size=1000, save_step=500, valid_iter=10):
        with tf.name_scope('Train'):
            self.generator = self.dataset.aux_generator(self.batch_size, self.nStacks, normalize=True,
                                                        sample_set='train')
            self.valid_gen = self.dataset.aux_generator(self.batch_size, self.nStacks, normalize=True,
                                                        sample_set='valid')
            start_time = time.time()
            self.resume['loss'] = []
            self.resume['error'] = []
            for epoch in range(nEpochs):
                epoch_start_time = time.time()
                avg_cost = 0.
                cost = 0.
                print('Epoch :' + str(epoch) + '/' + str(nEpochs) + '\n')

                # Training Set
                for i in range(epoch_size):
                    percent = ((i + 1) / epoch_size) * 100
                    num = np.int(20 * percent / 100)
                    time2end = int((time.time() - epoch_start_time) * (100 - percent) / percent)
                    sys.stdout.write(
                        '\r Train: {0}>'.format("=" * num) + "{0}>".format(" " * (20 - num)) + '||' +
                        str(percent)[:4] + '%' + ' -cost: ' + str(cost)[:6] + ' -avg_loss: ' +
                        str(avg_cost)[:5] + ' -timeToEnd: ' + str(time2end) + ' sec.'
                    )
                    sys.stdout.flush()

                    img_train, gt_train, weight_train = next(self.generator)
                    if i % save_step == 0:
                        if self.w_loss:
                            _, c, summary = self.Session.run(
                                [self.train_rmsprop, self.loss, self.train_op],
                                {self.img: img_train, self.gt_maps: gt_train, self.weights: weight_train}
                            )
                        else:
                            _, c, summary = self.Session.run(
                                [self.train_rmsprop, self.loss, self.train_op],
                                {self.img: img_train, self.gt_maps: gt_train}
                            )
                        # Save summary (Loss + Error)
                        self.train_summary.add_summary(summary, epoch * epoch_size + i)
                        self.train_summary.flush()
                    else:
                        if self.w_loss:
                            _, c, = self.Session.run(
                                [self.train_rmsprop, self.loss],
                                {self.img: img_train, self.gt_maps: gt_train, self.weights: weight_train}
                            )
                        else:
                            _, c, = self.Session.run(
                                [self.train_rmsprop, self.loss],
                                {self.img: img_train, self.gt_maps: gt_train}
                            )

                    cost += c
                    avg_cost += c / epoch_size
                epoch_finish_time = time.time()

                if self.w_loss:
                    weight_summary = self.Session.run(
                        self.weight_op,
                        {self.img: img_train, self.gt_maps: gt_train, self.weights: weight_train}
                    )
                else:
                    weight_summary = self.Session.run(
                        self.weight_op,
                        {self.img: img_train, self.gt_maps: gt_train}
                    )

                self.train_summary.add_summary(weight_summary, epoch)
                self.train_summary.flush()

                print('Epoch ' + str(epoch) + '/' + str(nEpochs) + ' done in ' + str(
                    int(epoch_finish_time - epoch_start_time)) + ' sec.' + ' -avg_time/batch: ' + str(
                    ((epoch_finish_time - epoch_start_time) / epoch_size))[:4] + ' sec.')
                with tf.name_scope('save'):
                    self.saver.save(self.Session,
                                    os.path.join('checkpoints/', str(self.name + '_' + str(epoch + 1))))
                self.resume['loss'].append(cost)

                # Validation Set
                error_array = np.array([0.0] * len(self.point_error))
                for i in range(valid_iter):
                    img_valid, gt_valid, w_valid = next(self.valid_gen)
                    error_pred = self.Session.run(self.point_error,
                                                  feed_dict={self.img: img_valid, self.gt_maps: gt_valid})
                    error_array += np.array(error_pred, dtype=np.float32) / valid_iter
                print('--Avg. Error =', str((np.sum(error_array) / len(error_array)) * 100)[:6], '%')
                self.resume['error'].append(np.sum(error_array) / len(error_array))
                valid_summary = self.Session.run(self.test_op, feed_dict={self.img: img_valid, self.gt_maps: gt_valid})
                self.test_summary.add_summary(valid_summary, epoch)
                self.test_summary.flush()

            print('Training Done')
            print('Resume:')
            print('  Epochs: ' + str(nEpochs))
            print('  n. Images: ' + str(nEpochs * epoch_size * self.batch_size))
            print('  Final Loss: ' + str(cost))
            print('  Relative Loss: ' + str(100 * self.resume['loss'][-1] / (self.resume['loss'][0] + 0.1)) + '%')
            print('  Relative Improvement: ' + str((self.resume['error'][0] - self.resume['error'][-1]) * 100) + '%')
            print('  Training Time: ' + str(datetime.timedelta(seconds=time.time() - start_time)))

    def training_init(self, nEpochs=10, epoch_size=1000, save_step=500, load=None):
        """
        Initialize the training.
        :param nEpochs: Number of epochs to train
        :param epoch_size: Size of one epoch
        :param save_step: Step to save 'train' summary (has to be lower than epoch size)
        :param load: Model to load (None if training from scratch)
        """
        with tf.name_scope('Session'):
            with tf.device(self.gpu):
                self._init_session()
                self._define_saver_summary()
                if load is not None:
                    self.saver.restore(self.Session, load)
                self._train(nEpochs, epoch_size, save_step, valid_iter=10)

    def weighted_bce_loss(self):
        self.bce_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.output, labels=self.gt_maps),
                                       name='cross_entropy_loss')
        e1 = tf.expand_dims(self.weights, axis=1, name='exp_dim1')
        e2 = tf.expand_dims(e1, axis=1, name='exp_dim2')
        e3 = tf.expand_dims(e2, axis=1, name='exp_dim3')
        return tf.multiply(e3, self.bce_loss, name='lossW')

    def _error_computation(self):
        self.point_error = []
        for i in range(len(self.points)):
            self.point_error.append(
                error(
                    self.output[:, self.nStacks - 1, :, :, i],
                    self.gt_maps[:, self.nStacks - 1, :, :, i],
                    self.batch_size
                )
            )

    def _define_saver_summary(self, summary=True):
        if self.logdir_train is None or self.logdir_test is None:
            raise ValueError('Train/Test directory not assigned')
        else:
            with tf.device(self.cpu):
                self.saver = tf.train.Saver(max_to_keep=10)
            if summary:
                with tf.device(self.gpu):
                    self.train_summary = tf.summary.FileWriter(self.logdir_train, tf.get_default_graph())
                    self.test_summary = tf.summary.FileWriter(self.logdir_test)

    def _init_session(self):
        print('Session initialization')
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.9)
        self.Session = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))
        t_start = time.time()
        self.Session.run(self.init)
        print('Sess initialized in ' + str(int(time.time() - t_start)) + ' sec.')

    def _graph_sphinx(self):
        with tf.name_scope('model'):
            pass

    def _graph_hourglass(self):
        with tf.name_scope('hourglass'):
            net = conv_layer(self.img, 64, 6, 2, 'conv1')
            net = batch_norm(net, self.training)
            net = bottleneck(net, 128, 32, 1, self.training, name='res1')
            net = max_pool(net, 2, 2, 'max_pool')
            net = bottleneck(net, int(self.nFeats / 2), stride=1, training=self.training, name='res2')
            net = bottleneck(net, self.nFeats, stride=1, training=self.training, name='res3')

            final_out = []
            with tf.name_scope('stacks'):
                with tf.name_scope('stage_0'):
                    hg = hourglass(net, self.nLow, self.nFeats, 'hourglass')
                    drop = dropout(hg, self.dropout_rate, self.training, 'dropout')
                    ll = conv_layer(drop, self.nFeats, 1, 1)
                    ll = batch_norm(ll, self.training)
                    ll_ = conv_layer(ll, self.nFeats, 1, 1, 'll')
                    out = conv_layer(ll, self.out_dim, 1, 1, 'out')
                    out_ = conv_layer(out, self.nFeats, 1, 1, 'out_')
                    sum_ = tf.add_n([out_, net, ll_], name='merge')
                    final_out.append(out)
                for i in range(1, self.nStacks - 1):
                    with tf.name_scope('stage_' + str(i)):
                        hg = hourglass(sum_, self.nLow, self.nFeats, 'hourglass')
                        drop = dropout(hg, self.dropout_rate, self.training, 'dropout')
                        ll = conv_layer(drop, self.nFeats, 1, 1, 'conv')
                        ll = batch_norm(ll, self.training)
                        ll_ = conv_layer(ll, self.nFeats, 1, 1, 'll')
                        out = conv_layer(ll, self.out_dim, 1, 1, 'out')
                        out_[i] = conv_layer(out, self.nFeats, 1, 1, 'out_')
                        sum_ = tf.add_n([out_, sum_, ll_], name='merge')
                        final_out.append(out)
                with tf.name_scope('stage_' + str(self.nStacks - 1)):
                    hg = hourglass(sum_, self.nLow, self.nFeats, 'hourglass')
                    drop = dropout(hg, self.dropout_rate, self.training, 'dropout')
                    ll = conv_layer(drop, self.nFeats, 1, 1, 'conv')
                    ll = batch_norm(ll, self.training)
                    out = conv_layer(ll, self.out_dim, 1, 1, 'out')
                    final_out.append(out)
            return tf.stack(final_out, axis=1, name='output')

    def _argmax(self, tensor):
        """
        ArgMax
        :param tensor: 2D - Tensor (height x width)
        :return: Tuple of max position
        """
        reshape = tf.reshape(tensor, [-1])
        arg_max = tf.argmax(reshape, 0)
        return arg_max // tensor.get_shape().as_list()[0], arg_max % tensor.get_shape().as_list()[0]

    def _graph_resnet(self, model='resnet_50', training=True):
        units = RESNET_50_UNIT
        if model is 'resnet_101':
            units = RESNET_101_UNIT
        if model is 'resnet_152':
            units = RESNET_152_UNIT
        if model is 'resnet_200':
            units = RESNET_200_UNIT
        blocks = [
            block('block1', bottleneck, [(256, 64, 1)] * (units[0] - 1) + [(256, 64, 2)]),
            block('block2', bottleneck, [(512, 128, 1)] * (units[1] - 1) + [(512, 128, 2)]),
            block('block3', bottleneck, [(1024, 256, 1)] * (units[2] - 1) + [(1024, 256, 2)]),
            block('block4', bottleneck, [(2048, 512, 1)] * units[3])
        ]

        net = conv_layer(self.img, 64, 7, 2, 'conv')
        net = max_pool(net, 3, 2)
        net = stack_block_dense(net, blocks, self.training)
        feature = net
        # global average pooling
        with tf.name_scope('global_avg_pool'):
            net = tf.reduce_mean(net, [1, 2], keep_dims=True, name='net_flat')
        net = fc_layer(net, self.num_classes, name='fc')
        prediction = tf.nn.softmax(net, name='logits')
        if training:
            return feature, prediction
        else:
            return feature
