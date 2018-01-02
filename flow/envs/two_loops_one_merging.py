from flow.envs.base_env import SumoEnvironment
from flow.core import rewards

from gym.spaces.box import Box
from gym.spaces.tuple_space import Tuple

import numpy as np


class TwoLoopsMergeEnv(SumoEnvironment):
    """
    Fully functional environment. Differs from the SimpleAccelerationEnvironment
    in loop_accel in that vehicles in this environment may follow one of two
    routes (continuously on the smaller ring or merging in and out of the
    smaller ring). Accordingly, the single global reference for position is
    replaced with a reference in each ring.
    """

    @property
    def action_space(self):
        """
        See parent class.

        Actions are a set of accelerations from max-deacc to max-acc for each
        rl vehicle.
        """
        return Box(low=-np.abs(self.env_params.max_deacc),
                   high=self.env_params.max_acc,
                   shape=(self.vehicles.num_rl_vehicles, ))

    @property
    def observation_space(self):
        """
        See parent class.

        An observation is an array the velocities, positions, and edges for
        each vehicle
        """
        self.obs_var_labels = ["speed", "pos"]
        speed = Box(low=0, high=np.inf, shape=(self.vehicles.num_vehicles,))
        absolute_pos = Box(low=0., high=np.inf, shape=(self.vehicles.num_vehicles,))
        return Tuple((speed, absolute_pos))

        # headway = Box(low=0., high=np.inf,
        #               shape=(self.vehicles.num_rl_vehicles + 1,))
        # speed = Box(low=0, high=np.inf,
        #             shape=(self.vehicles.num_rl_vehicles + 1,))
        # return Tuple((speed, headway))

    def apply_rl_actions(self, rl_actions):
        """
        See parent class.
        """
        sorted_rl_ids = [veh_id for veh_id in self.sorted_ids
                         if veh_id in self.vehicles.get_rl_ids()]
        self.apply_acceleration(sorted_rl_ids, rl_actions)

    def compute_reward(self, state, rl_actions, **kwargs):
        """
        See parent class

        Rewards high system-level velocities in the rl vehicles.
        """
        # return np.mean(self.vehicles.get_speed())
        return rewards.desired_velocity(self, fail=kwargs["fail"])

    def get_state(self, **kwargs):
        """
        See parent class.

        The state is an array the velocities, edge counts, and relative
        positions on the edge, for each vehicle.
        """
        vel = self.vehicles.get_speed(self.sorted_ids)
        pos = [self.get_x_by_id(veh_id) for veh_id in self.sorted_ids]
        # is_rl = [int(veh_id in self.rl_ids) for veh_id in self.sorted_ids]

        # # normalize the speed
        # normalized_vel = np.array(vel) / 30.
        #
        # # normalize the position
        # normalized_pos = np.array(pos) / self.scenario.length

        # return np.array([normalized_vel, normalized_pos, is_rl]).T
        return np.array([vel, pos]).T

        # # The first observation is the position of the closest human vehicle
        # # behind the intersection and its speed. Each subsequent observation is
        # # the headway for the rl vehicle and the vehicle's speed
        # sorted_rl_ids = [veh_id for veh_id in self.sorted_ids if veh_id in self.rl_ids]
        # headways = self.vehicles.get_headway(sorted_rl_ids)
        # speeds = self.vehicles.get_speed(sorted_rl_ids)
        #
        # sorted_human_ids = [veh_id for veh_id in self.sorted_ids if veh_id not in self.rl_ids]
        # r = self.scenario.net_params.additional_params["ring_radius"]
        # junction_length = 0.3
        # intersection_length = 25.5
        # lead_gap = 2 * np.pi * r + junction_length + 2 * intersection_length \
        #     - self.get_x_by_id(sorted_human_ids[0])
        # lead_vel = self.vehicles.get_speed(sorted_human_ids[0])
        #
        # return np.array([[lead_vel] + speeds,
        #                  [lead_gap] + headways]).T

    def sort_by_position(self):
        """
        See parent class

        Instead of being sorted by a global reference, vehicles in this
        environment are sorted with regards to which ring this currently
        reside on.
        """
        pos = [self.get_x_by_id(veh_id) for veh_id in self.vehicles.get_ids()]
        sorted_indx = np.argsort(pos)
        sorted_ids = np.array(self.vehicles.get_ids())[sorted_indx]

        sorted_human_ids = [veh_id for veh_id in sorted_ids
                            if veh_id not in self.vehicles.get_rl_ids()]
        # sorted_human_ids = sorted_human_ids[::-1]

        sorted_rl_ids = [veh_id for veh_id in sorted_ids
                         if veh_id in self.vehicles.get_rl_ids()]
        # sorted_rl_ids = sorted_rl_ids[::-1]

        sorted_ids = sorted_human_ids + sorted_rl_ids

        return sorted_ids, None
        # return self.ids, None


class TwoLoopsMergePOEnv(TwoLoopsMergeEnv):
    """
    POMDP version of two-loop merge env
    """

    @property
    def observation_space(self):
        """
        See parent class.

        Observes the RL vehicle, the two vehicles preceding and following the RL
        vehicle on the inner ring, as well as the two vehicles closest to
        merging in.

        WARNING: only supports 1 RL vehicle

        An observation is an array the velocities, positions, and edges for
        each vehicle
        """
        self.n_preceding = 2  # FIXME(cathywu) see below
        self.n_following = 2  # FIXME(cathywu) see below
        self.n_merging_in = 2
        self.n_obs_vehicles = 1 + self.n_preceding + self.n_following + \
                        self.n_merging_in
        self.obs_var_labels = ["speed", "pos", "dist_to_merge"]
        speed = Box(low=0, high=np.inf, shape=(self.n_obs_vehicles,))
        absolute_pos = Box(low=0., high=np.inf, shape=(self.n_obs_vehicles,))
        # dist_to_merge = Box(low=-1, high=1, shape=(1,))
        queue_length = Box(low=0, high=np.inf, shape=(1,))
        return Tuple((speed, absolute_pos, queue_length))

    def get_state(self, **kwargs):
        """
        See parent class.

        The state is an array the velocities, edge counts, and relative
        positions on the edge, for each vehicle.
        """
        vel = np.zeros(self.n_obs_vehicles)
        pos = np.zeros(self.n_obs_vehicles)

        outer_ids = self.sorted_ids[-self.scenario.n_outer_vehicles:]
        # Retrieve the vel/pos for the vehicles closest to merging in
        merge_in_count = 0
        merge_in_id = None
        for i, id in enumerate(outer_ids):
            p = self.get_x_by_id(id)
            if p < self.scenario.length_loop:
                continue
            if merge_in_id is None:
                merge_in_id = i + (len(
                    self.sorted_ids)-self.scenario.n_outer_vehicles)
            pos[self.n_obs_vehicles-self.n_merging_in+merge_in_count] = p
            v = self.vehicles.get_speed(id)
            vel[self.n_obs_vehicles-self.n_merging_in+merge_in_count] = v
            merge_in_count += 1
            if merge_in_count >= self.n_merging_in:
                break
        if merge_in_id is None:
            merge_in_id = len(self.sorted_ids)

        rl_vehID = self.vehicles.get_rl_ids()[0]
        rl_srtID = self.sorted_ids.index(rl_vehID)

        # FIXME(cathywu) hardcoded for self.num_preceding = 2
        lead_id1 = self.sorted_ids[(rl_srtID-1) % merge_in_id]
        lead_id2 = self.sorted_ids[(rl_srtID-2) % merge_in_id]
        # FIXME(cathywu) hardcoded for self.num_following = 2
        follow_id1 = self.sorted_ids[(rl_srtID+1) % merge_in_id]
        follow_id2 = self.sorted_ids[(rl_srtID+2) % merge_in_id]
        vehicles = [rl_vehID, lead_id1, lead_id2, follow_id1, follow_id2]

        vel[:self.n_obs_vehicles - self.n_merging_in] = np.array(
            self.vehicles.get_speed(vehicles))
        pos[:self.n_obs_vehicles - self.n_merging_in] = np.array(
            [self.get_x_by_id(veh_id) for veh_id in vehicles])
        # is_rl = [int(veh_id in self.rl_ids) for veh_id in self.sorted_ids]

        # normalize the speed
        # FIXME(cathywu) pull user-defined speed limit?
        normalized_vel = np.array(vel) / 30.

        # normalize the position
        normalized_pos = np.array(pos) / self.scenario.length

        # Compute number of vehicles in the outer ring
        queue_length = np.zeros(1)
        queue_length[0] = len(self.sorted_ids) - merge_in_id

        return np.array([normalized_vel, normalized_pos, queue_length]).T
        # return np.array([vel, pos]).T
