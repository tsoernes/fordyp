import time
from functools import partial
from multiprocessing import Pool

import numpy as np

from runners.runner import Runner


class AvgRunner(Runner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self):
        t = time.time()
        n_runs = self.pp['avg_runs']
        simproc = partial(self.sim_proc, self.stratclass, self.pp, reseed=True)
        # Net runs use same np seed for all runs; other strats do not
        with Pool() as p:
            results = p.map(simproc, range(n_runs))
        results = [r for r in results if r[0] != 1 and r[0] is not None]
        if not results:
            self.logger.error("NO RESULTS")
            return
        n_events = self.pp['n_events']
        results = np.array(results)
        self.logger.error(
            f"\n{n_runs}x{n_events} events finished with speed"
            f" {(n_runs*n_events)/(time.time()-t):.0f} events/second"
            f"\nAverage cumulative block probability over {n_runs} episodes:"
            f" {np.mean(results[:,0]):.4f}"
            f" with standard deviation {np.std(results[:,0]):.5f}"
            f"\nAverage cumulative handoff block probability"
            f" {np.mean(results[:,1]):.4f}"
            f" with standard deviation {np.std(results[:,1]):.5f}"
            f"\n{results}")
        # TODO Plot average cumulative over time
