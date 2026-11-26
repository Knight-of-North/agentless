import argparse

from agentless.util.utils import load_jsonl


def cost(fp, prompt_price_per_million=5.0, completion_price_per_million=15.0):
    def flatten_trajs(record, trajs):
        if isinstance(record, dict):
            trajs.append(record)
            return
        for r in record:
            flatten_trajs(r, trajs)

    data = load_jsonl(fp)

    sum_prompt, sum_completion = 0, 0
    total = 0

    for record in data:
        for key in record:
            if "traj" not in key:
                continue
            trajs = []
            flatten_trajs(record[key], trajs)
            for r in trajs:
                if "usage" in r:
                    prompt_tokens = r["usage"]["prompt_tokens"]
                    if "completion_tokens" in r["usage"]:
                        completion_tokens = r["usage"]["completion_tokens"]
                    else:
                        completion_tokens = 0
                    sum_prompt += prompt_tokens
                    sum_completion += completion_tokens

        total += 1

    cost = (
        sum_prompt * prompt_price_per_million / 1000000
        + sum_completion * completion_price_per_million / 1000000
    )
    tokens = sum_prompt + sum_completion
    avg_cost = cost / total if total else 0.0
    avg_tokens = tokens / total if total else 0.0

    print(f"Total cost: {cost = }")
    print(f"Avg cost: {avg_cost}")

    print(f"Total tokens: {tokens = }")
    print(f"Avg tokens: {avg_tokens}")


def embedding_cost(fp):
    data = load_jsonl(fp)

    cost = 0
    total = 0

    for record in data:
        cost += 0.02 * record["traj"]["usage"]["embedding_tokens"] / 1000000
        total += 1

    print(f"Total cost: {cost = }")
    print(f"Avg cost: {total}: {cost / total if total else 0.0}")

    return cost


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_file", type=str, required=True)
    parser.add_argument("--embedding_cost", action="store_true")
    parser.add_argument("--prompt_price_per_million", type=float, default=5.0)
    parser.add_argument("--completion_price_per_million", type=float, default=15.0)

    args = parser.parse_args()

    if args.embedding_cost:
        embedding_cost(args.output_file)
    else:
        cost(
            args.output_file,
            prompt_price_per_million=args.prompt_price_per_million,
            completion_price_per_million=args.completion_price_per_million,
        )


if __name__ == "__main__":
    main()
