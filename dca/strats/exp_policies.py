import numpy as np

from gridfuncs import GF


def _nominal_eligible_idxs(chs, cell):
    """Return the indecies of 'chs' which correspond to nominal channels in 'cell'"""
    nominal_eligible_idxs = [i for i, ch in enumerate(chs) if GF.nom_chs_mask[cell][ch]]
    return nominal_eligible_idxs


class Policy:
    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, temp, chs, qvals_dense, cell):
        """

        :param temp: Temperature/epsilon
        :param chs: Channels to select from
        :param qvals_dense: Corresponding q-values for each ch in chs
        :param cell: Cell in which action is to be executed

        Returns (ch, idx, prob) for selected channel where ch=chs[idx]
        and prob is probability of picking action
        """
        pass


class EpsGreedy(Policy):
    """Epsilon greedy action selection"""

    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, epsilon, chs, qvals_dense, *args):
        if np.random.random() < epsilon:
            # Choose an eligible channel at random
            idx = np.random.randint(0, len(chs))
            p = epsilon / len(chs)
        else:
            # Choose greedily
            idx = np.argmax(qvals_dense)
            p = 1 - epsilon
        ch = chs[idx]
        return ch, idx, p


class NomGreedyEpsGreedy(Policy):
    """Epsilon greedy where exploration actions are selected greedily from
    the cells nominal channels, if possible"""

    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, epsilon, chs, qvals_dense, cell):
        if np.random.random() < epsilon:
            # Choose at greedily from nominal channels, else at random
            nom_elig_idxs = _nominal_eligible_idxs(chs, cell)
            if nom_elig_idxs:
                nidx = np.argmax(qvals_dense[nom_elig_idxs])
                idx = nom_elig_idxs[nidx]
                p = epsilon
            else:
                idx = np.random.randint(0, len(chs))
                p = epsilon / len(chs)
        else:
            # Choose greedily
            idx = np.argmax(qvals_dense)
            p = 1 - epsilon
        ch = chs[idx]
        return ch, idx, p


class EpsNomGreedy(Policy):
    """Select nominal channel with prob. epsilon, greedy otherwise"""

    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, epsilon, chs, qvals_dense, cell):
        if np.random.random() < epsilon:
            # Choose at greedily from nominal channels, else at random
            nom_elig_idxs = _nominal_eligible_idxs(chs, cell)
            if nom_elig_idxs:
                nidx = np.argmax(qvals_dense[nom_elig_idxs])
                idx = nom_elig_idxs[nidx]
                p = epsilon
            else:
                idx = np.argmax(qvals_dense)
                p = 1 - epsilon
        else:
            # Choose greedily
            idx = np.argmax(qvals_dense)
            p = 1 - epsilon
        ch = chs[idx]
        return ch, idx, p


class NomGreedyGreedy(Policy):
    """Channel is always greedily selected from the cells nominal channels, if one
    is available, else greedily from the eligible channels.
    """

    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, epsilon, chs, qvals_dense, cell):
        nom_elig_idxs = _nominal_eligible_idxs(chs, cell)
        if nom_elig_idxs:
            nidx = np.argmax(qvals_dense[nom_elig_idxs])
            idx = nom_elig_idxs[nidx]
        else:
            idx = np.argmax(qvals_dense)
        ch = chs[idx]
        return ch, idx, 1


class NomFixedGreedy(Policy):
    """The lowest numbered nominal channel is selected, if any, else greedy selection"""

    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, temp, chs, qvals_dense, cell):
        nom_elig_idxs = _nominal_eligible_idxs(chs, cell)
        if nom_elig_idxs:
            idx = nom_elig_idxs[0]
        else:
            idx = np.argmax(qvals_dense)
        ch = chs[idx]
        return ch, idx, 1


class Boltzmann(Policy):
    """Boltzmann selection"""

    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, temp, chs, qvals_dense, *args):
        max_idx = np.argmax(qvals_dense)
        scaled = np.exp((qvals_dense - qvals_dense[max_idx]) / temp)
        probs = scaled / np.sum(scaled)
        idx = np.random.choice(range(len(chs)), p=probs)
        if idx == max_idx:
            p = 1 / probs[idx]
        else:
            p = 0
        return chs[idx], idx, p


class NomBoltzmann(Policy):
    """Boltzmann selection from the nominal channels, if any, else boltzmann
    selection from the eligible channels"""

    def __init__(self, *args, **kwargs):
        pass

    def select_action(self, temp, chs, qvals_dense, cell):
        nom_elig_idxs = _nominal_eligible_idxs(chs, cell)
        if nom_elig_idxs:
            nom_qvals = qvals_dense[nom_elig_idxs]
            scaled = np.exp((nom_qvals - np.max(nom_qvals)) / temp)
            probs = scaled / np.sum(scaled)
            nom_elig_idx = np.random.choice(range(len(nom_elig_idxs)), p=probs)
            idx = nom_elig_idxs[nom_elig_idx]
            p = probs[nom_elig_idx]
        else:
            scaled = np.exp((qvals_dense - np.max(qvals_dense)) / temp)
            probs = scaled / np.sum(scaled)
            idx = np.random.choice(range(len(chs)), p=probs)
            p = probs[idx]
        ch = chs[idx]
        return ch, idx, p


class NomBoltzmann2(Policy):
    def __init__(self, c):
        c = 1.5 if c is None else c
        self.nom_temps_mults = np.arange(c, 1, (1 - c) / 10)
        self.nom_qval_consts = np.arange(10, 1, -0.1)

    def select_action(self, temp, chs, qvals_dense, cell):
        nom_elig_idxs = []
        nom_elig_temps_mults = []
        nom_elig_qval_consts = []
        for i, ch in enumerate(chs):
            if GF.nom_chs_mask[cell][ch]:
                nom_elig_idxs.append(i)
                nom_i = i % 10
                nom_elig_temps_mults.append(self.nom_temps_mults[nom_i])
                nom_elig_qval_consts.append(self.nom_qval_consts[nom_i])

        temps = np.repeat(temp, len(chs))
        temps[nom_elig_idxs] *= nom_elig_temps_mults
        qvals_dense[nom_elig_idxs] += nom_elig_qval_consts

        scaled = np.exp((qvals_dense - np.max(qvals_dense)) / temps)
        probs = scaled / np.sum(scaled)
        idx = np.random.choice(range(len(chs)), p=probs)
        ch = chs[idx]
        # print(nom_elig_idxs, temps, qvals_dense, probs, "\n")
        return ch, idx, 1


class BoltzmannGumbel(Policy):
    """Implements Boltzmann-Gumbel exploration (BGE) adapted for Q learning
    based on the paper Boltzmann Exploration Done Right
    (https://arxiv.org/pdf/1705.10257.pdf).
    BGE is invariant with respect to the mean of the rewards but not their
    variance. The parameter C, which defaults to 1, can be used to correct for
    this, and should be set to the least upper bound on the standard deviation
    of the rewards.
    BGE is only available for training, not testing. For testing purposes, you
    can achieve approximately the same result as BGE after training for N steps
    on K actions with parameter C by using the BoltzmannQPolicy and setting
    tau = C/sqrt(N/K)."""

    def __init__(self, c, n_channels=70):
        c = 5.3 if c is None else c
        assert c > 0
        self.c = c
        self.action_counts = np.ones(n_channels)

    def select_action(self, temp, chs, qvals_dense, cell):
        assert qvals_dense.ndim == 1, qvals_dense.ndim

        beta = self.c / np.sqrt(self.action_counts[chs])
        z = np.random.gumbel(size=qvals_dense.shape)

        perturbation = beta * z
        perturbed_q_values = qvals_dense + perturbation
        idx = np.argmax(perturbed_q_values)
        ch = chs[idx]

        self.action_counts[ch] += 1
        # Calculating the probs. is difficult according to paper;
        # pretend greedy selection
        return ch, idx, 1

    def action_prob(self, beta, qvals, idx):
        # TODO
        # mult by beta somewhere
        # handle pos/neg signs of k differently
        k = qvals - qvals[idx]  # abs?

        ek = (1 - np.exp(-k))
        p = np.exp(ek) / ek

        np.multiply.reduce(p[:idx]) * np.multiply.reduce(p[idx:])
        pass


class EpsNomBoltzmannGumbel(BoltzmannGumbel):
    """With probability eps, choose greedily from the nominal channels, else
    bgumbel explore"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def select_action(self, epsilon, chs, qvals_dense, cell):
        if np.random.random() < epsilon:
            # Choose at greedily from nominal channels, else at random
            nom_elig_idxs = _nominal_eligible_idxs(chs, cell)
            if nom_elig_idxs:
                nidx = np.argmax(qvals_dense[nom_elig_idxs])
                idx = nom_elig_idxs[nidx]
                ch = chs[idx]
                # NOTE This might not be optimal
                self.action_counts[ch] += 1
                return ch, idx, 1
        return super().select_action(epsilon, chs, qvals_dense, cell)


exp_pol_funcs = {
    'eps_greedy': EpsGreedy,
    'nom_greedy': NomGreedyGreedy,
    'nom_eps_greedy': NomGreedyEpsGreedy,
    'eps_nom_greedy': EpsNomGreedy,
    'boltzmann': Boltzmann,
    'nom_boltzmann': NomBoltzmann,
    'nom_boltzmann2': NomBoltzmann2,
    'nom_fixed_greedy': NomFixedGreedy,
    'eps_nom_bgumbel': EpsNomBoltzmannGumbel,
    'bgumbel': BoltzmannGumbel
}  # yapf: disable
