#!/usr/bin/env python3
"""
Agent Dispatcher for CodingPass Multi-Agent System.
Wraps 'pi coding agent' with `--provider ttu-vllm --model qwen3.8-flash-nvfp4`.
"""

import argparse
import subprocess
import sys
import os

ROLE_PROMPTS = {
    "coder": (
        "You are Pi-Coder, a senior software engineer specializing in C, C++, Python (FastAPI), "
        "and modern web frontend (React/TypeScript/Tailwind). "
        "Your task is to implement clean, robust, well-documented, and production-ready code. "
        "Adhere strictly to project specifications and write safe code with error handling."
    ),
    "reviewer": (
        "You are Pi-Reviewer, a principal software architect and security auditor. "
        "Your role is to rigorously review code changes, identify security risks "
        "(especially in subprocess sandboxing, memory management, infinite loops, fork bombs), "
        "edge cases, and propose actionable fixes or approve the implementation."
    ),
    "tester": (
        "You are Pi-Tester, a QA automation engineer. "
        "Your role is to write comprehensive unit, integration, and edge-case tests, "
        "execute them, and verify that the system handles various scenarios correctly "
        "(including compilation errors, timeouts, large stdin/stdout, and crashes)."
    )
}

def run_agent(role: str, prompt: str, files: list = None, print_only: bool = True, session_id: str = None):
    cmd = [
        "pi",
        "--provider", "ttu-vllm",
        "--model", "qwen3.8-flash-nvfp4",
    ]
    
    system_prompt = ROLE_PROMPTS.get(role, ROLE_PROMPTS["coder"])
    cmd.extend(["--append-system-prompt", system_prompt])
    
    if print_only:
        cmd.append("-p")
        
    if session_id:
        cmd.extend(["--session-id", session_id])
        
    if files:
        for f in files:
            cmd.append(f"@{f}")
            
    cmd.append(prompt)
    
    print(f"🚀 Dispatching to Agent [{role.upper()}] with prompt: {prompt[:80]}...")
    res = subprocess.run(cmd, capture_output=False)
    return res.returncode

def main():
    parser = argparse.ArgumentParser(description="Dispatch tasks to Pi Coding Agents")
    parser.add_argument("role", choices=["coder", "reviewer", "tester"], help="Agent role")
    parser.add_argument("prompt", help="Instruction prompt for the agent")
    parser.add_argument("--files", "-f", nargs="*", help="File context to attach", default=[])
    parser.add_argument("--interactive", "-i", action="store_true", help="Run interactively")
    parser.add_argument("--session", "-s", help="Session ID", default=None)
    
    args = parser.parse_args()
    code = run_agent(
        role=args.role,
        prompt=args.prompt,
        files=args.files,
        print_only=not args.interactive,
        session_id=args.session
    )
    sys.exit(code)

if __name__ == "__main__":
    main()
