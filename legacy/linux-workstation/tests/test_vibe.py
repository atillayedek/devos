from devos import vibe


def test_slugify():
    assert vibe.slugify("Next.js + Supabase Marketplace!") == "next-js-supabase-marketplace"
    assert vibe.slugify("   ") == "project"


def test_agents_never_auto_approve():
    for agent in vibe.AGENTS:
        if agent.id == "local":
            continue
        cmd = " ".join(vibe.agent_command(agent, "build an app"))
        for flag in ("--dangerously-skip-permissions", "--yolo", "--full-auto", "--dangerously-bypass"):
            assert flag not in cmd
