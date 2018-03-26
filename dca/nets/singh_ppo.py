import numpy as np
import tensorflow as tf

import gridfuncs_numba as NGF
from nets.net import Net
from nets.utils import get_trainable_vars


class PPOSinghNet(Net):
    def __init__(self, pre_conv=False, double_net=False, *args, **kwargs):
        """
        Afterstate value net
        """
        self.name = "SinghNet"
        self.pre_conv = pre_conv
        self.double_net = double_net
        super().__init__(name=self.name, *args, **kwargs)
        self.weight_beta = self.pp['weight_beta']
        self.weight_beta_decay = self.pp['weight_beta_decay']
        self.avg_reward = [0]

    def _build_vnet(self, freps, name):
        with tf.variable_scope('model/' + name) as scope:
            value_layer = tf.layers.Dense(
                units=1,
                kernel_initializer=tf.zeros_initializer(),
                kernel_regularizer=self.dense_regularizer,
                use_bias=False,
                activation=None)
            value = value_layer.apply(tf.layers.flatten(freps))
            self.weight_vars.append(value_layer.kernel)
            self.weight_names.append(value_layer.name)
            # NOTE TODO either gotta have 7x7x70 outputs, or input cell
            # also gotta think about a hidden layer before value/policy
            trainable_vars = get_trainable_vars(scope)
        return value, trainable_vars

    def _build_pnet(self, freps, name):
        with tf.variable_scope('model/' + name) as scope:
            policy = tf.keras.layers.LocallyConnected2D(
                filters=70,
                kernel_size=1,
                padding="valid",
                kernel_initializer=tf.zeros_initializer(),
                use_bias=self.pp['conv_bias'],
                activation=None)(freps)
            # policy_layer = tf.layers.Dense(
            #     units=70,
            #     kernel_initializer=tf.zeros_initializer(),
            #     kernel_regularizer=self.dense_regularizer,
            #     use_bias=False,
            #     activation=None)
            # policy = policy_layer.apply(tf.layers.flatten(freps))
            # self.weight_vars.append(policy_layer.kernel)
            # self.weight_names.append(policy_layer.name)
            trainable_vars = get_trainable_vars(scope)
        return policy, trainable_vars

    def build(self):
        # frepshape = [None, self.rows, self.cols, self.n_channels * 3 + 1]
        frepshape = [None, self.rows, self.cols, self.n_channels + 1]
        self.freps = tf.placeholder(tf.float32, frepshape, "feature_reps")
        self.value_target = tf.placeholder(tf.float32, [None, 1], "value_target")
        self.advantage = tf.placeholder(tf.float32, [None], "advantage")
        self.cells = tf.placeholder(tf.int32, [None, 2], "cell")
        self.action = tf.placeholder(tf.int32, [None], "action")
        self.policy_in = tf.placeholder(tf.float32, [70], "pol_in")
        self.old_neglogpac = tf.placeholder(tf.float32, [None], "old_neglogpac")

        self.value, online_vf_vars = self._build_vnet(self.freps, "online-vf")
        self.conv_policy, online_pg_vars = self._build_pnet(self.freps, "online-pg")
        nrange = tf.range(tf.shape(self.freps)[0], name="cellrange")
        ncells = tf.concat([tf.expand_dims(nrange, axis=1), self.cells], axis=1)
        self.policy = tf.gather_nd(self.conv_policy, ncells)

        self.err = self.value_target - self.value
        self.vf_loss = tf.losses.mean_squared_error(
            labels=self.value_target, predictions=self.value)

        CLIPRANGE = 0.2
        self.neglogpac_out = self.neglogp(self.policy_in, self.action)
        self.neglogpac = self.neglogp(self.policy, self.action)
        ratio = tf.exp(self.old_neglogpac - self.neglogpac)
        pg_losses = -self.advantage * ratio
        pg_losses2 = -self.advantage * tf.clip_by_value(ratio, 1.0 - CLIPRANGE,
                                                        1.0 + CLIPRANGE)
        pg_loss = tf.reduce_mean(tf.maximum(pg_losses, pg_losses2))
        # pgnetloss = pg_loss - entropy * ent_coef + vf_loss * vf_coef
        # trainer = tf.train.AdamOptimizer(learning_rate=self.pp, epsilon=1e-5)
        trainer = tf.train.GradientDescentOptimizer(learning_rate=self.pp['net_lr'])
        grads = trainer.compute_gradients(pg_loss, online_pg_vars)
        self.do_train_pg = trainer.apply_gradients(grads)
        return self.vf_loss, online_vf_vars

    @staticmethod
    def neglogp(logits, x):
        one_hot_actions = tf.one_hot(x, logits.get_shape().as_list()[-1])
        return tf.nn.softmax_cross_entropy_with_logits(
            logits=logits, labels=one_hot_actions)

    def forward(self, grid, ce_type, cell, chs):
        # u = tf.random_uniform(tf.shape(self.policy))
        # self.sample_action = tf.argmax(elig_policy - tf.log(-tf.log(u)), axis=-1)
        frep = NGF.feature_rep(grid)
        policy = self.sess.run(self.policy, {self.freps: [frep]})[0]
        idx = np.argmax(policy[chs])
        ch = chs[idx]
        neglogpac = self.sess.run(self.neglogpac_out, {
            self.policy_in: policy,
            self.action: [ch]
        })
        next_freps = NGF.incremental_freps(grid, frep, cell, ce_type, np.array([ch]))
        value = self.sess.run(
            self.value,
            feed_dict={self.freps: next_freps},
        )[0, 0]
        # print(value, ch, type(ch), neglogpac)
        return value, ch, neglogpac

    def backward(self, freps, chs, rewards, next_freps, neglogpac):
        next_value = self.sess.run(self.value, feed_dict={self.freps: next_freps})
        value_target = rewards + next_value - self.avg_reward
        data = {
            self.freps: freps,
            self.value_target: value_target,
        }
        # Backprop value net
        _, loss, lr, err = self.sess.run([self.do_train, self.vf_loss, self.lr, self.err],
                                         data)
        self.sess.run(
            [self.do_train_pg], {
                self.freps: freps,
                self.advantage: value_target[0],
                self.old_neglogpac: [neglogpac],
                self.action: chs
            })
        if self.pp['avg_reward']:
            self.avg_reward += self.weight_beta * err[0][0]
            # self.weight_beta *= self.weight_beta_decay
            # print(self.avg_reward)
        return loss, lr, err