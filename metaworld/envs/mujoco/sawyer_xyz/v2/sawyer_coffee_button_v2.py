from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
from gymnasium.spaces import Box

from metaworld.envs.asset_path_utils import full_v2_path_for
from metaworld.envs.mujoco.sawyer_xyz.sawyer_xyz_env import RenderMode, SawyerXYZEnv
from metaworld.envs.mujoco.utils import reward_utils
from metaworld.types import InitConfigDict


class SawyerCoffeeButtonEnvV2(SawyerXYZEnv):
    def __init__(
        self,
        render_mode: RenderMode | None = None,
        camera_name: str | None = None,
        camera_id: int | None = None,
    ) -> None:
        self.max_dist = 0.03

        hand_low = (-0.5, 0.4, 0.05)
        hand_high = (0.5, 1.0, 0.5)
        obj_low = (-0.1, 0.8, -0.001)
        obj_high = (0.1, 0.9, +0.001)
        # goal_low[3] would be .1, but objects aren't fully initialized until a
        # few steps after reset(). In that time, it could be .01
        goal_low = obj_low + np.array([-0.001, -0.22 + self.max_dist, 0.299])
        goal_high = obj_high + np.array([+0.001, -0.22 + self.max_dist, 0.301])

        super().__init__(
            hand_low=hand_low,
            hand_high=hand_high,
            render_mode=render_mode,
            camera_name=camera_name,
            camera_id=camera_id,
        )

        self.init_config: InitConfigDict = {
            "obj_init_pos": np.array([0, 0.9, 0.28]),
            "obj_init_angle": 0.3,
            "hand_init_pos": np.array([0.0, 0.4, 0.2]),
        }
        self.goal = np.array([0, 0.78, 0.33])
        self.obj_init_pos = self.init_config["obj_init_pos"]
        self.obj_init_angle = self.init_config["obj_init_angle"]
        self.hand_init_pos = self.init_config["hand_init_pos"]

        self._random_reset_space = Box(
            np.array(obj_low), np.array(obj_high), dtype=np.float64
        )
        self.goal_space = Box(np.array(goal_low), np.array(goal_high), dtype=np.float64)

    @property
    def model_name(self) -> str:
        return full_v2_path_for("sawyer_xyz/sawyer_coffee.xml")

    @SawyerXYZEnv._Decorators.assert_task_is_set
    def evaluate_state(
        self, obs: npt.NDArray[np.float64], action: npt.NDArray[np.float32]
    ) -> tuple[float, dict[str, Any]]:
        (
            reward,
            tcp_to_obj,
            tcp_open,
            obj_to_target,
            near_button,
            button_pressed,
        ) = self.compute_reward(action, obs)

        info = {
            "success": float(obj_to_target <= 0.02),
            "near_object": float(tcp_to_obj <= 0.07),
            "grasp_success": float(tcp_open > 0),
            "grasp_reward": near_button,
            "in_place_reward": button_pressed,
            "obj_to_target": obj_to_target,
            "unscaled_reward": reward,
        }

        return reward, info

    @property
    def _target_site_config(self) -> list[tuple[str, npt.NDArray[Any]]]:
        assert (
            self._target_pos is not None
        ), "`reset_model()` must be called before `_target_site_config`."
        return [("coffee_goal", self._target_pos)]

    def _get_id_main_object(self):
        return None

    def _get_pos_objects(self) -> npt.NDArray[Any]:
        return self._get_site_pos("buttonStart")

    def _get_quat_objects(self) -> npt.NDArray[Any]:
        return np.array([1.0, 0.0, 0.0, 0.0])

    def _set_obj_xyz(self, pos: npt.NDArray[Any]) -> None:
        qpos = self.data.qpos.flatten()
        qvel = self.data.qvel.flatten()
        qpos[0:3] = pos.copy()
        qvel[9:15] = 0
        self.set_state(qpos, qvel)

    def reset_model(self) -> npt.NDArray[np.float64]:
        self._reset_hand()

        self.obj_init_pos = self._get_state_rand_vec()
        self.model.body("coffee_machine").pos = self.obj_init_pos

        pos_mug = self.obj_init_pos + np.array([0.0, -0.22, 0.0])
        self._set_obj_xyz(pos_mug)

        pos_button = self.obj_init_pos + np.array([0.0, -0.22, 0.3])
        self._target_pos = pos_button + np.array([0.0, self.max_dist, 0.0])

        return self._get_obs()

    def compute_reward(
        self, action: npt.NDArray[Any], obs: npt.NDArray[np.float64]
    ) -> tuple[float, float, float, float, float, float]:
        assert (
            self._target_pos is not None
        ), "`reset_model()` must be called before `compute_reward()`."
        del action
        obj = obs[4:7]
        tcp = self.tcp_center

        tcp_to_obj = float(np.linalg.norm(obj - tcp))
        tcp_to_obj_init = float(np.linalg.norm(obj - self.init_tcp))
        obj_to_target = abs(self._target_pos[1] - obj[1])

        tcp_closed = max(obs[3], 0.0)
        near_button = reward_utils.tolerance(
            tcp_to_obj,
            bounds=(0, 0.05),
            margin=tcp_to_obj_init,
            sigmoid="long_tail",
        )
        button_pressed = reward_utils.tolerance(
            obj_to_target,
            bounds=(0, 0.005),
            margin=self.max_dist,
            sigmoid="long_tail",
        )

        reward = 4 * reward_utils.hamacher_product(tcp_closed, near_button)
        reward += np.exp(-tcp_to_obj / 0.2)
        
        if tcp_to_obj <= 0.07:
            reward += 8 * button_pressed

        return (reward, tcp_to_obj, obs[3], obj_to_target, near_button, button_pressed)
