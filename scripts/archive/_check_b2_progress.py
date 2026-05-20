import os
base = r'd:\mcp-governance-main - A\results\neutral_multitool_real_llm\bursty'
if not os.path.exists(base):
    print(f"Dir not found: {base}")
else:
    for gw in sorted(os.listdir(base)):
        gw_dir = os.path.join(base, gw)
        if not os.path.isdir(gw_dir):
            continue
        for run in sorted(os.listdir(gw_dir)):
            ap = os.path.join(gw_dir, run, "steps_agents.csv")
            if os.path.exists(ap):
                n = sum(1 for _ in open(ap)) - 1
                print(f"{gw}/{run}: {n} agents")
            else:
                print(f"{gw}/{run}: (no agents CSV yet)")
