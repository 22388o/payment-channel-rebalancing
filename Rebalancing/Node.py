import simpy


class Node:
    def __init__(self, env, node_parameters, rebalancing_parameters, demand_estimates, verbose):
        self.env = env
        self.balances = {"L": node_parameters["initial_balance_L"], "R": node_parameters["initial_balance_R"]}
        self.capacities = {"L": node_parameters["capacity_L"], "R": node_parameters["capacity_R"]}
        self.fees = [node_parameters["base_fee"], node_parameters["proportional_fee"]]
        self.on_chain_budget = node_parameters["on_chain_budget"]
        self.swap_IN_amounts_in_progress = {"L": 0, "R": 0}
        self.swap_OUT_amounts_in_progress = {"L": 0, "R": 0}
        self.rebalancing_parameters = rebalancing_parameters
        self.verbose = verbose
        self.demand_estimates = demand_estimates
        self.net_demands = {"L": 0, "R": 0}

        self.node_processor = simpy.Resource(env, capacity=1)
        self.rebalancing_locks = {"L": simpy.Resource(env, capacity=1), "R": simpy.Resource(env, capacity=1)}     # max 1 rebalancing operation active at a time
        # self.rebalance_requests = {"L": self.env.event(), "R": self.env.event()}
        self.time_to_check = self.env.event()

        self.balance_history_times = []
        self.balance_history_values = {"L": [], "R": []}
        self.total_fortune_including_pending_swaps_times = []
        self.total_fortune_including_pending_swaps_values = []
        self.rebalancing_history_start_times = []
        self.rebalancing_history_end_times = []
        self.rebalancing_history_types = []
        self.rebalancing_history_amounts = []
        self.rebalancing_history_results = []


    def calculate_fees(self, amount):
        return self.fees[0] + amount*self.fees[1]

    def execute_feasible_transaction(self, t):
        # Calling this function requires checking for transaction feasibility beforehand. The function itself does not perform any checks, and this could lead to negative balances if misused.

        self.balances[t.previous_node] += t.amount
        self.balances[t.next_node] -= (t.amount - self.calculate_fees(t.amount))
        self.balance_history_times.append(self.env.now)
        self.balance_history_values["L"].append(self.balances["L"])
        self.balance_history_values["R"].append(self.balances["R"])
        t.status = "SUCCEEDED"

        if self.verbose:
            print("Time {:.2f}: SUCCESS: Transaction {} processed.".format(self.env.now, t))
            print("Time {:.2f}: New balances are {}, on-chain = {}, IN-pending = {}, OUT-pending = {}.".format(self.env.now, self.balances, self.on_chain_budget, self.swap_IN_amounts_in_progress["L"]+self.swap_IN_amounts_in_progress["R"], self.swap_OUT_amounts_in_progress["L"]+self.swap_OUT_amounts_in_progress["R"]))

    def reject_transaction(self, t):
        t.status = "FAILED"
        if self.verbose:
            print("Time {:.2f}: FAILURE: Transaction {} rejected.".format(self.env.now, t))
            print("Time {:.2f}: New balances are {}, on-chain = {}, IN-pending = {}, OUT-pending = {}.".format(self.env.now, self.balances, self.on_chain_budget, self.swap_IN_amounts_in_progress["L"]+self.swap_IN_amounts_in_progress["R"], self.swap_OUT_amounts_in_progress["L"]+self.swap_OUT_amounts_in_progress["R"]))

    def process_transaction(self, t):
        if (t.amount >= self.calculate_fees(t.amount)) and (t.amount <= self.capacities[t.previous_node] - self.balances[t.previous_node]) and (t.amount - self.calculate_fees(t.amount) <= self.balances[t.next_node]):
            self.execute_feasible_transaction(t)
        else:
            self.reject_transaction(t)
        # t.cleared.succeed()
        self.time_to_check.succeed()
        self.time_to_check = self.env.event()

        self.total_fortune_including_pending_swaps_times.append(self.env.now)
        total_fortune_including_pending_swaps = self.balances["L"] + self.balances["R"] + self.on_chain_budget + self.swap_IN_amounts_in_progress["L"] + self.swap_IN_amounts_in_progress["R"] + self.swap_OUT_amounts_in_progress["L"] + self.swap_OUT_amounts_in_progress["R"]
        self.total_fortune_including_pending_swaps_values.append(total_fortune_including_pending_swaps)


    def perform_rebalancing_if_needed(self, neighbor):
        if self.verbose:
            print("Time {:.2f}: SWAP check performed for channel N-{}.".format(self.env.now, neighbor))

        if self.rebalancing_locks[neighbor].count == 0:     # if no rebalancing in progress in the N-neighbor channel
            with self.rebalancing_locks[neighbor].request() as rebalance_request:  # Generate a request event
                yield rebalance_request

                if self.rebalancing_parameters["rebalancing_policy"] == "none":
                    pass

                elif self.rebalancing_parameters["rebalancing_policy"] == "autoloop":
                    midpoint = self.capacities[neighbor] * (self.rebalancing_parameters["lower_threshold"] + self.rebalancing_parameters["upper_threshold"]) / 2

                    if self.balances[neighbor] < self.rebalancing_parameters["lower_threshold"] * self.capacities[neighbor]:    # SWAP-IN
                        swap_amount = midpoint - self.balances[neighbor]
                        yield self.env.process(self.swap_in(neighbor, swap_amount, rebalance_request))
                    elif self.balances[neighbor] > self.rebalancing_parameters["upper_threshold"] * self.capacities[neighbor]:      # SWAP-OUT
                        swap_amount = self.balances[neighbor] - midpoint
                        yield self.env.process(self.swap_out(neighbor, swap_amount, rebalance_request))
                    else:
                        pass    # no rebalancing needed
                        if self.verbose:
                            print("Time {:.2f}: SWAP not needed in channel N-{}.". format(self.env.now, neighbor))
                #
                # elif self.rebalancing_parameters["rebalancing_policy"] == "autoloop-infrequent":
                #     midpoint = self.capacities[neighbor] * (self.rebalancing_parameters["lower_threshold"] + self.rebalancing_parameters["upper_threshold"]) / 2
                #     other_neighbor = "R" if neighbor == "L" else "L"
                #     self.net_demands[neighbor] = self.demand_estimates[neighbor] - (self.demand_estimates[other_neighbor] - self.calculate_fees(self.demand_estimates[other_neighbor]))
                #
                #     if self.balances[neighbor] < self.rebalancing_parameters["T_conf"] * self.net_demands[neighbor]:    # SWAP-IN
                #         swap_amount = midpoint - self.balances[neighbor]
                #         yield self.env.process(self.swap_in(neighbor, swap_amount, rebalance_request))
                #     elif self.balances[neighbor] > self.rebalancing_parameters["T_conf"] * self.net_demands[neighbor]:      # SWAP-OUT
                #         swap_amount = self.balances[neighbor] - midpoint
                #         yield self.env.process(self.swap_out(neighbor, swap_amount, rebalance_request))
                #     else:
                #         pass    # no rebalancing needed
                #         if self.verbose:
                #             print("Time {:.2f}: SWAP not needed in channel N-{}.". format(self.env.now, neighbor))

                elif self.rebalancing_parameters["rebalancing_policy"] == "loopmax":
                    other_neighbor = "R" if neighbor == "L" else "L"
                    self.net_demands[neighbor] = self.demand_estimates[neighbor] - (self.demand_estimates[other_neighbor] - self.calculate_fees(self.demand_estimates[other_neighbor]))
                    if self.net_demands[neighbor] < 0:  # SWAP-IN
                        expected_time_to_depletion = self.balances[neighbor] / (- self.net_demands[neighbor])
                        if expected_time_to_depletion - self.rebalancing_parameters["check_interval"] < self.rebalancing_parameters["T_conf"]:
                            safety_margin_in_coins = - self.net_demands[neighbor] / self.rebalancing_parameters["safety_margins_in_minutes"][neighbor]
                            swap_amount = self.max_swap_in_amount(neighbor) - safety_margin_in_coins
                            # swap_amount = self.max_swap_in_amount(neighbor)
                            yield self.env.process(self.swap_in(neighbor, swap_amount, rebalance_request))
                        else:
                            pass
                            if self.verbose:
                                print("Time {:.2f}: SWAP not needed in channel N-{}.". format(self.env.now, neighbor))
                    elif self.net_demands[neighbor] > 0:    # SWAP-OUT
                        expected_time_to_saturation = (self.capacities[neighbor] - self.balances[neighbor]) / self.net_demands[neighbor]
                        if expected_time_to_saturation - self.rebalancing_parameters["check_interval"] < self.rebalancing_parameters["T_conf"]:
                            safety_margin_in_coins = self.net_demands[neighbor] / self.rebalancing_parameters["safety_margins_in_minutes"][neighbor]
                            swap_amount = self.balances[neighbor] - safety_margin_in_coins
                            yield self.env.process(self.swap_out(neighbor, swap_amount, rebalance_request))
                        elif self.verbose:
                            print("Time {:.2f}: SWAP not needed in channel N-{}.". format(self.env.now, neighbor))
                    else:
                        pass    # no rebalancing needed
                        if self.verbose:
                            print("Time {:.2f}: SWAP not needed in channel N-{}.". format(self.env.now, neighbor))
        else:
            pass  # if rebalancing already in progress, do not check again if rebalancing is needed
            if self.verbose:
                print("Time {:.2f}: SWAP already in progress in channel N-{}.".format(self.env.now, neighbor))

    def max_swap_in_amount(self, neighbor):
        return min(self.on_chain_budget * (1 - self.rebalancing_parameters["server_swap_fee"]) - self.rebalancing_parameters["miner_fee"], self.capacities[neighbor])

    def swap_in(self, neighbor, swap_amount, rebalance_request):
        swap_in_fees = swap_amount * self.rebalancing_parameters["server_swap_fee"] + self.rebalancing_parameters["miner_fee"]

        self.rebalancing_history_start_times.append(self.env.now)
        self.rebalancing_history_types.append(neighbor + "-IN")
        self.rebalancing_history_amounts.append(swap_amount)
        if self.verbose:
            print("Time {:.2f}: SWAP-IN initiated in channel N-{} with amount {}.".format(self.env.now, neighbor, swap_amount))

        if swap_amount <= 0:
            if self.verbose:
                print("Time {:.2f}: SWAP-IN aborted due to violation of safety margin in channel N-{}.".format(self.env.now, neighbor))
            self.rebalancing_history_results.append("FAILED")
            self.rebalancing_history_end_times.append(self.env.now)
        elif self.on_chain_budget < swap_amount + swap_in_fees:
            if self.verbose:
                print("Time {:.2f}: SWAP-IN failed in channel N-{} with amount {}.".format(self.env.now, neighbor, swap_amount))
            self.rebalancing_history_results.append("FAILED")
            self.rebalancing_history_end_times.append(self.env.now)
        else:
            self.on_chain_budget -= (swap_amount + swap_in_fees)
            self.swap_IN_amounts_in_progress[neighbor] += swap_amount
            yield self.env.timeout(self.rebalancing_parameters["T_conf"])

            self.swap_IN_amounts_in_progress[neighbor] -= swap_amount
            if self.capacities[neighbor] - self.balances[neighbor] < swap_amount:
                if self.verbose:
                    print("Time {:.2f}: SWAP-IN failed in channel N-{} with amount {}.".format(self.env.now, neighbor, swap_amount))
                self.on_chain_budget += (swap_amount + swap_in_fees)
                self.rebalancing_history_results.append("FAILED")
                self.rebalancing_history_end_times.append(self.env.now)
            else:
                self.balances[neighbor] += swap_amount

                self.rebalancing_history_results.append("SUCCEEDED")
                self.rebalancing_history_end_times.append(self.env.now)
                if self.verbose:
                    print("Time {:.2f}: SWAP-IN completed in channel N-{} with amount {}.".format(self.env.now, neighbor, swap_amount))
                    print("Time {:.2f}: New balances are {}, on-chain = {}, IN-pending = {}, OUT-pending = {}.".format(self.env.now, self.balances, self.on_chain_budget, self.swap_IN_amounts_in_progress, self.swap_OUT_amounts_in_progress))
                # self.rebalance_requests[neighbor].succeed()
                # self.rebalance_requests[neighbor] = self.env.event()
                # return neighbor + "-in"
        self.rebalancing_locks[neighbor].release(rebalance_request)

    def swap_out(self, neighbor, swap_amount, rebalance_request):
        swap_out_fees = swap_amount * self.rebalancing_parameters["server_swap_fee"] + self.rebalancing_parameters["miner_fee"]

        self.rebalancing_history_start_times.append(self.env.now)
        self.rebalancing_history_types.append(neighbor + "-OUT")
        self.rebalancing_history_amounts.append(swap_amount)
        if self.verbose:
            print("Time {:.2f}: SWAP-OUT initiated in channel N-{} with amount {}.".format(self.env.now, neighbor, swap_amount))

        if swap_amount <= 0:
            if self.verbose:
                print("Time {:.2f}: SWAP-OUT aborted due to violation of safety margin in channel N-{}.".format(self.env.now, neighbor))
            self.rebalancing_history_results.append("FAILED")
            self.rebalancing_history_end_times.append(self.env.now)
        elif (self.balances[neighbor] < swap_amount) or (swap_amount < swap_out_fees):  # check the swap-out constraints
            if self.verbose:
                print("Time {:.2f}: SWAP-OUT failed in channel N-{} with amount {}.".format(self.env.now, neighbor, swap_amount))
            self.rebalancing_history_results.append("FAILED")
            self.rebalancing_history_end_times.append(self.env.now)
        else:
            self.balances[neighbor] -= swap_amount
            self.swap_OUT_amounts_in_progress[neighbor] += (swap_amount - swap_out_fees)
            yield self.env.timeout(self.rebalancing_parameters["T_conf"])
            self.on_chain_budget += (swap_amount - swap_out_fees)
            self.swap_OUT_amounts_in_progress[neighbor] -= (swap_amount - swap_out_fees)

            self.rebalancing_history_results.append("SUCCEEDED")
            self.rebalancing_history_end_times.append(self.env.now)
            if self.verbose:
                print("Time {:.2f}: SWAP-OUT completed in channel N-{} with amount {}.".format(self.env.now, neighbor, swap_amount))
                print("Time {:.2f}: New balances are {}, on-chain = {}, IN-pending = {}, OUT-pending = {}.".format(self.env.now, self.balances, self.on_chain_budget, self.swap_IN_amounts_in_progress, self.swap_OUT_amounts_in_progress))
            # self.rebalance_requests[neighbor].succeed()
            # self.rebalance_requests[neighbor] = self.env.event()
            # return neighbor + "-out"
        self.rebalancing_locks[neighbor].release(rebalance_request)

    def run(self):
        while True:
            # yield self.rebalance_requests["L"] | self.rebalance_requests["R"]

            # For checking after clearing each transaction
            # yield self.time_to_check

            # For checking every some fixed time
            yield self.env.timeout(self.rebalancing_parameters["check_interval"])

            for neighbor in ["L", "R"]:
                self.env.process(self.perform_rebalancing_if_needed(neighbor))
                # yield self.env.process(self.perform_rebalancing_if_needed(neighbor))
            # yield self.env.process(self.perform_rebalancing_if_needed("L"))
