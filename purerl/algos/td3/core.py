import chex
import numpy as np
from jax import numpy as jnp
from typing import Any, Callable, Tuple
from flax import struct, linen as nn
from gymnax.environments.environment import Environment


class TD3Config(struct.PyTreeNode):
    # Non-static parameters
    env_params: Any
    gamma: chex.Scalar
    tau: chex.Scalar
    max_grad_norm: chex.Scalar
    learning_rate: chex.Scalar
    exploration_noise: chex.Scalar
    target_noise: chex.Scalar
    target_noise_clip: chex.Scalar
    policy_delay: int

    # Static parameters
    total_timesteps: int = struct.field(pytree_node=False)
    eval_freq: int = struct.field(pytree_node=False)
    actor: nn.Module = struct.field(pytree_node=False)
    critic: nn.Module = struct.field(pytree_node=False)
    env: Environment = struct.field(pytree_node=False)
    eval_callback: Callable = struct.field(pytree_node=False)
    buffer_size: int = struct.field(pytree_node=False)
    fill_buffer: int = struct.field(pytree_node=False)
    batch_size: int = struct.field(pytree_node=False)
    normalize_observations: bool = struct.field(pytree_node=False, default=False)
    skip_initial_evaluation: bool = struct.field(pytree_node=False, default=False)

    @property
    def agent(self):
        """So that config.agent.apply exists"""
        return self.actor

    @property
    def action_low(self):
        return self.env.action_space(self.env_params).low

    @property
    def action_high(self):
        return self.env.action_space(self.env_params).high

    @classmethod
    def from_dict(cls, config):
        import gymnax
        from copy import deepcopy
        from purerl.evaluate import make_evaluate
        from purerl.brax2gymnax import Brax2GymnaxEnv

        config = deepcopy(config)  # Because we're popping from it

        # Get env id and convert to gymnax environment and parameters
        env_kwargs = config.pop("env_kwargs", {})
        env_id = config.pop("env")
        if env_id.startswith("brax"):
            env = Brax2GymnaxEnv(env_id.split("/")[1], **env_kwargs)
            env_params = env.default_params
        else:
            env, env_params = gymnax.make(env_id, **env_kwargs)

        actor_kwargs = config.pop("actor_kwargs", {})
        activation = actor_kwargs.pop("activation", "relu")
        actor_kwargs["activation"] = getattr(nn, activation)
        action_range = (
            env.action_space(env_params).low,
            env.action_space(env_params).high,
        )
        action_dim = np.prod(env.action_space(env_params).shape)
        actor = TD3Actor(action_dim, action_range, **actor_kwargs)

        critic_kwargs = config.pop("critic_kwargs", {})
        activation = critic_kwargs.pop("activation", "relu")
        critic_kwargs["activation"] = getattr(nn, activation)
        critic_cls = nn.vmap(
            MLPQFunction,
            variable_axes={"params": 0},
            split_rngs={"params": True},
        )
        critic = critic_cls(**critic_kwargs)

        evaluate = make_evaluate(env, env_params, 200)
        return cls(
            env_params=env_params,
            actor=actor,
            critic=critic,
            env=env,
            eval_callback=evaluate,
            **config,
        )


class MLPQFunction(nn.Module):
    activation: Callable = nn.relu

    @nn.compact
    def __call__(self, obs, action):
        seq = nn.Sequential(
            [nn.Dense(64), self.activation, nn.Dense(64), self.activation, nn.Dense(1)]
        )
        q = seq(jnp.concatenate([obs.reshape(obs.shape[0], -1), action], axis=-1))
        return jnp.squeeze(q, axis=-1)


class TD3Actor(nn.Module):
    """Equivaluent to DDPGActor"""

    action_dim: int
    action_range: Tuple[float, float]
    activation: Callable = nn.relu

    @property
    def action_loc(self):
        return (self.action_range[1] + self.action_range[0]) / 2

    @property
    def action_scale(self):
        return (self.action_range[1] - self.action_range[0]) / 2

    @nn.compact
    def __call__(self, x):
        x = nn.Dense(64)(x)
        x = self.activation(x)
        x = nn.Dense(64)(x)
        x = self.activation(x)
        x = nn.Dense(self.action_dim)(x)
        x = jnp.tanh(x)

        action = self.action_loc + x * self.action_scale
        return action

    def act(self, obs, rng):
        action = self(obs)
        return action
