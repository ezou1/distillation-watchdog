/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Port of the watchdog server, for links from the other servers' pages. Default 8010. */
  readonly VITE_WATCHDOG_PORT?: string;
  /** Port of the agent chat server, for links from the other servers' pages. Default 8003. */
  readonly VITE_AGENT_PORT?: string;
  /** Port of the normal traffic generator. Default 8001. */
  readonly VITE_NORMAL_GEN_PORT?: string;
  /** Port of the attack traffic generator. Default 8002. */
  readonly VITE_ATTACK_GEN_PORT?: string;
}
