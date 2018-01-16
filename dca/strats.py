import signal
from typing import Tuple

import numpy as np

from environment import Env
from eventgen import CEvent
from grid import RhombusAxialGrid
from nets.acnet import ACNet
from replaybuffer import ExperienceBuffer, ReplayBuffer


class Strat:
    def __init__(self, pp, logger, pid="", gui=None, *args, **kwargs):
        self.rows = pp['rows']
        self.cols = pp['cols']
        self.n_channels = pp['n_channels']
        self.save = pp['save_exp_data']
        self.batch_size = pp['batch_size']
        self.pp = pp
        self.logger = logger

        grid = RhombusAxialGrid(self.rows, self.cols, self.n_channels,
                                self.logger)
        self.env = Env(self.pp, grid, self.logger, pid)
        self.grid = self.env.grid.state
        self.replaybuffer = ReplayBuffer(pp['buffer_size'], self.rows,
                                         self.cols, self.n_channels)

        self.quit_sim = False
        self.t = 1
        signal.signal(signal.SIGINT, self.exit_handler)

    def exit_handler(self, *args):
        """
        Graceful exit on ctrl-c signal from
        command line or on 'q' key-event from gui.
        """
        self.logger.warn("\nPremature exit")
        self.quit_sim = True

    def simulate(self):
        cevent = self.env.init_calls()
        ch = self.get_init_action(cevent)

        # Discrete event simulation
        for i in range(self.pp['n_events']):
            if self.quit_sim:
                break  # Gracefully exit to print stats, clean up etc.

            self.t, ce_type, cell = cevent[0:3]

            if ch is not None:
                # if self.save or self.batch_size > 1:
                grid = np.copy(self.grid)  # Copy before state is modified

            reward, next_cevent = self.env.step(ch)
            next_ch = self.get_action(next_cevent, grid, cell, ch, reward,
                                      ce_type)
            if (self.save or self.batch_size > 1) \
                    and ch is not None \
                    and ce_type != CEvent.END:
                # Only add (s, a, r, s', a') tuples for which the events in
                # s is not an END events, and for which there is an
                # available action a.
                # If there is no available action, that is, there are no
                # free channels which to assign, the neural net is not used
                # for selection and so it should not be trained on that data.
                # END events are not trained on either because the network is
                # supposed to predict the q-values for different channel
                # assignments; however the channels available for reassignment
                # are always busy in a grid corresponding to an END event.
                next_grid = np.copy(self.grid)
                next_cell = next_cevent[2]
                self.replaybuffer.add(grid, cell, ch, reward, next_grid,
                                      next_cell)

            if i % self.pp['log_iter'] == 0 and i > 0:
                self.fn_report()
            # TODO Do in net class instead
            # if self.pp['net'] and \
            #         i % self.pp['net_copy_iter'] == 0 and i > 0:
            #     self.update_target()
            ch, cevent = next_ch, next_cevent
        self.env.stats.end_episode(reward)
        self.fn_after()
        if self.save:
            self.replaybuffer.save_experience_to_disk()
        if self.quit_sim and self.pp['hopt']:
            # Don't want to return actual block prob for incomplete sims when
            # optimizing params, because block prob is much lower at sim start
            return 1
        return self.env.stats.block_prob_cum

    def get_init_action(self, next_cevent):
        raise NotImplementedError

    def get_action(self, next_cevent, cell, ch, reward):
        raise NotImplementedError

    def fn_report(self):
        """
        Report stats for different strategies
        """
        pass

    def fn_after(self):
        """
        Cleanup
        """
        pass


class RLStrat(Strat):
    def __init__(self, pp, *args, **kwargs):
        super().__init__(pp, *args, **kwargs)
        self.epsilon = pp['epsilon']
        self.epsilon_decay = pp['epsilon_decay']
        self.alpha = pp['alpha']
        self.alpha_decay = pp['alpha_decay']
        self.gamma = pp['gamma']

    def fn_report(self):
        self.env.stats.report_rl(self.epsilon, self.alpha)

    def update_qval(self, cell, ch, target_q):
        raise NotImplementedError

    def get_qvals(self, cell, *args, **kwargs):
        """
        Different strats may use additional arguments,
        depending on the features
        """
        raise NotImplementedError

    def get_init_action(self, cevent):
        ch, _ = self.optimal_ch(ce_type=cevent[1], cell=cevent[2])
        return ch

    def get_action(self, next_cevent, grid, cell, ch: int, reward,
                   ce_type) -> int:
        """
        Return a channel to be (re)assigned for 'next_cevent'.
        'cell' and 'ch' specify the previously executed action.
        """
        next_ce_type, next_cell = next_cevent[1:3]
        # Choose A' from S'
        next_ch, next_qval = self.optimal_ch(next_ce_type, next_cell)
        # If there's no action to take, or no action was taken,
        # don't update q-value at all
        if ce_type != CEvent.END and ch is not None and next_ch is not None:
            # Observe reward from previous action, and
            # update q-values with one-step lookahead
            self.update_qval(cell, ch, reward, next_qval)
        return next_ch

    def policy_eps_greedy_exp(self, qvals, chs):
        """Epsilon greedy action selection with expontential decay"""
        if np.random.random() < self.epsilon:
            # Choose an eligible channel at random
            ch = np.random.choice(chs)
        else:
            # Choose greedily
            idx = np.argmax(qvals[chs])
            ch = chs[idx]
        self.epsilon *= self.epsilon_decay  # Epsilon decay

        return ch

    def policy_eps_greedy_lil(self, qvals, chs):
        epsilon = self.epsilon / np.sqrt(self.t / 256)
        if np.random.random() < epsilon:
            # Choose an eligible channel at random
            ch = np.random.choice(chs)
        else:
            # Choose greedily
            idx = np.argmax(qvals[chs])
            ch = chs[idx]

        return ch

    def policy_boltzmann(self, qvals, chs):
        scaled = np.exp((qvals[chs] - np.max(qvals[chs])) / self.temp)
        probs = scaled / np.sum(scaled)
        ch = np.random.choice(chs, p=probs)
        return ch

    def optimal_ch(self, ce_type, cell) -> Tuple[int, float]:
        # NOTE this isn't really the optimal ch since
        # it's chosen in an epsilon-greedy fashion
        """
        Select the channel fitting for assignment that
        that has the maximum q-value in an epsilon-greedy fasion,
        or select the channel for termination that has the minimum
        q-value in a greedy fashion.

        Return (ch, qval) where 'qval' is the q-value for the
        selected channel.
        'ch' is None if no channel is eligible for assignment.
        """
        inuse = np.nonzero(self.grid[cell])[0]
        n_used = len(inuse)

        if ce_type == CEvent.NEW or ce_type == CEvent.HOFF:
            chs = self.env.grid.get_free_chs(cell)
        else:
            # Channels in use at cell, including channel scheduled
            # for termination. The latter is included because it might
            # be the least valueable channel, in which case no
            # reassignment is done on call termination.
            chs = inuse
        if len(chs) == 0:
            # No channels available for assignment,
            # or no channels in use to reassign
            assert ce_type != CEvent.END
            return (None, None)

        qvals = self.get_qvals(cell=cell, n_used=n_used, ce_type=ce_type)
        # Selecting a ch for reassigment is always greedy because no learning
        # is done on the reassignment actions.
        if ce_type == CEvent.END:
            idx = np.argmin(qvals[chs])
            ch = chs[idx]
        else:
            ch = self.policy_eps_greedy_exp(qvals, chs)

        # If qvals blow up ('NaN's and 'inf's), ch becomes none.
        if ch is None:
            self.logger.error(f"{ce_type}\n{chs}\n{qvals}\n\n")
            raise Exception

        self.logger.debug(
            f"Optimal ch: {ch} for event {ce_type} of possibilities {chs}")
        return (ch, qvals[ch])


class SARSA(RLStrat):
    """
    State consists of coordinates and the number of used channels in that cell.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "qvals[r][c][n_used][ch] = v"
        # Assigning channel 'ch' to the cell at row 'r', col 'c'
        # has q-value 'v' given that 'n_used' channels are already
        # in use at that cell.
        self.qvals = np.zeros((self.rows, self.cols, self.n_channels,
                               self.n_channels))

    def get_qvals(self, cell, n_used, *args, **kwargs):
        return self.qvals[cell][n_used]

    def update_qval(self, cell, ch, reward, next_qval):
        assert type(ch) == np.int64
        n_used = np.count_nonzero(self.grid[cell])
        target_q = reward + self.gamma * next_qval
        td_err = target_q - self.get_qvals(cell, n_used)[ch]
        self.qvals[cell][n_used][ch] += self.alpha * td_err
        self.alpha *= self.alpha_decay


class N_STEP_SARSA(RLStrat):
    """
    State consists of coordinates and the number of used channels in that cell.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "qvals[r][c][n_used][ch] = v"
        # Assigning channel 'ch' to the cell at row 'r', col 'c'
        # has q-value 'v' given that 'n_used' channels are already
        # in use at that cell.
        self.qvals = np.zeros((self.rows, self.cols, self.n_channels,
                               self.n_channels))
        self.n = 10
        self.rewards = []
        # State-action pairs are not update until n rewards are experienced
        self.state_actions = []

    def get_qvals(self, cell, n_used, *args, **kwargs):
        return self.qvals[cell][n_used]

    def update_qval(self, cell, ch: np.int64, reward, next_qval):
        assert type(ch) == np.int64
        self.rewards.append(reward)
        n_used = np.count_nonzero(self.grid[cell])
        self.state_actions.append((cell, n_used, ch))
        if len(self.rewards) < self.n:
            return
        if len(self.rewards) > self.n:
            del self.rewards[0]
            del self.state_actions[0]
        target_q = 0  # reward + self.gamma * next_qval
        for i in range(0, self.n):
            target_q += (self.gamma**i) * self.rewards[i]
        target_q += (self.gamma**self.n) * next_qval
        t_cell, t_n_used, t_ch = self.state_actions[0]
        td_err = target_q - self.get_qvals(t_cell, t_n_used)[t_ch]
        self.qvals[t_cell][t_n_used][t_ch] += self.alpha * td_err
        self.alpha *= self.alpha_decay


class TT_SARSA(RLStrat):
    """
    State consists of cell coordinates and the number of used channels.
    If the number of used channels exceeds 'k', their values are
    aggregated to 'k'.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.k = 30
        self.qvals = np.zeros((self.rows, self.cols, self.k, self.n_channels))

    def get_qvals(self, cell, n_used, *args, **kwargs):
        return self.qvals[cell][min(self.k - 1, n_used)]

    def update_qval(self, cell, ch: np.int64, reward, next_qval):
        assert type(ch) == np.int64
        n_used = np.count_nonzero(self.grid[cell])
        target_q = reward + self.gamma * next_qval
        td_err = target_q - self.get_qvals(cell, n_used)[ch]
        self.qvals[cell][min(self.k - 1, n_used)][ch] += self.alpha * td_err
        self.alpha *= self.alpha_decay


class RS_SARSA(RLStrat):
    """
    State consists of cell coordinates only
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qvals = np.zeros((self.rows, self.cols, self.n_channels))

    def get_qvals(self, cell, *args, **kwargs):
        return self.qvals[cell]

    def update_qval(self, cell, ch: np.int64, reward, next_qval):
        assert type(ch) == np.int64
        target_q = reward + self.gamma * next_qval
        td_err = target_q - self.get_qvals(cell)[ch]
        self.qvals[cell][ch] += self.alpha * td_err
        self.alpha *= self.alpha_decay


class NetStrat(RLStrat):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.losses = []

    def fn_report(self):
        self.env.stats.report_net(self.losses)
        super().fn_report()

    def fn_after(self):
        self.net.save_model()
        self.net.save_timeline()
        self.net.sess.close()

    def get_action(self, next_cevent, grid, cell, ch, reward, ce_type) -> int:
        """
        Return a channel to be (re)assigned for 'next_cevent'.
        'cell' and 'ch' specify the previously executed action, and 'grid'
        the state before that action was executed.
        """
        next_ce_type, next_cell = next_cevent[1:3]
        # Choose A' from S'
        next_ch, next_qval = self.optimal_ch(next_ce_type, next_cell)
        # If there's no action to take, or no action was taken,
        # don't update q-value at all
        if ce_type != CEvent.END and ch is not None and next_ch is not None:
            # Observe reward from previous action, and
            # update q-values with one-step lookahead
            self.update_qval(grid, cell, ch, reward, self.grid, next_cell,
                             next_ch)
        return next_ch

    def get_qvals(self, cell, ce_type, *args, **kwargs):
        if ce_type == CEvent.END:
            state = np.copy(self.grid)
            state[cell] = np.zeros(self.n_channels)
        else:
            state = self.grid
        qvals = self.net.forward(state, cell)
        return qvals


class SARSAQNet(NetStrat):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.batch_size > 1:
            self.update_qval = self.update_qval_experience
            self.logger.warn("Using experience replay with batch"
                             f" size of {self.batch_size}")
        else:
            self.update_qval = self.update_qval_single
        from nets.qnet import QNet
        self.net = QNet(True, self.pp, self.logger, restore=False, save=False)

    def update_target(self):
        self.net.sess.run(self.net.copy_online_to_target)

    def update_qval_single(self, grid, cell, ch, reward, next_grid, next_cell,
                           next_ch):
        """ Update qval for one experience tuple"""
        loss = self.net.backward(grid, cell, [ch], [reward], next_grid,
                                 next_cell)
        self.losses.append(loss)
        if np.isinf(loss) or np.isnan(loss):
            self.quit_sim = True

    def update_qval_experience(self, *args):
        raise NotImplementedError
        """
        Update qval for pp['batch_size'] experience tuples,
        randomly sampled from the experience replay memory.
        """
        if len(self.replaybuffer) < self.pp['buffer_size']:
            # Can't backprop before exp store has enough experiences
            print("Not training" + str(len(self.replaybuffer)))
            return
        loss = self.net.backward(
            *self.replaybuffer.sample(self.pp['batch_size']))
        self.losses.append(loss)
        if np.isinf(loss) or np.isnan(loss):
            self.quit_sim = True


class ACNetStrat(NetStrat):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.net = ACNet(True, self.pp, self.logger, restore=False, save=False)
        self.exp_buffer = ExperienceBuffer()

    def forward(self, cell, ce_type):
        if ce_type == CEvent.END:
            state = np.copy(self.grid)
            state[cell] = np.zeros(self.n_channels)
        else:
            state = self.grid
        a, v = self.net.forward(state, cell)
        return a, v

    def get_action(self, next_cevent, grid, cell, ch: int, reward,
                   ce_type) -> int:
        """
        Return a channel to be (re)assigned for 'next_cevent'.
        'cell' and 'ch' specify the action that was executed on 'grid'
        resulting in 'reward'.
        """
        next_ce_type, next_cell = next_cevent[1:3]
        # Choose A' from S'
        next_ch, val = self.optimal_ch(next_ce_type, next_cell)
        # If there's no action to take, or no action was taken,
        # don't update q-value at all
        if ce_type != CEvent.END and ch is not None and next_ch is not None:
            # Observe reward from previous action, and
            # update q-values with one-step lookahead
            self.update_qval(grid, cell, ch, val, reward, self.grid, next_cell,
                             next_ch)
        return next_ch

    def optimal_ch(self, ce_type, cell) -> Tuple[int, float]:
        inuse = np.nonzero(self.grid[cell])[0]

        if ce_type == CEvent.NEW or ce_type == CEvent.HOFF:
            chs = self.env.grid.get_free_chs(cell)
        else:
            chs = inuse
        if len(chs) == 0:
            assert ce_type != CEvent.END
            return (None, None)

        ch, val = self.forward(cell=cell, ce_type=ce_type)
        # Selecting a ch for reassigment is always greedy because no learning
        # is done on the reassignment actions.

        # If vals blow up ('NaN's and 'inf's), ch becomes none.
        if np.isinf(val) or np.isnan(val):
            self.logger.error(f"{ce_type}\n{chs}\n{val}\n\n")
            raise Exception

        self.logger.debug(
            f"Optimal ch: {ch} for event {ce_type} of possibilities {chs}")
        return (ch, val)

    def update_qval(self, grid, cell, val, ch, reward, next_grid, next_cell,
                    next_ch):
        """
        Update qval for pp['batch_size'] experience tuple.
        """
        self.exp_buffer.add(grid, cell, val, ch, reward)
        if len(self.exp_buffer) < self.pp['n_step']:
            # Can't backprop before exp store has enough experiences
            return
        loss = self.net.backward(*self.exp_buffer.pop(self.pp['batch_size']))
        self.losses.append(loss)
        if np.isinf(loss) or np.isnan(loss):
            self.quit_sim = True
