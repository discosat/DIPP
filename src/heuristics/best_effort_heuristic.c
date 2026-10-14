#include "heuristics.h"
#include <time.h>
#include <stdbool.h>
#include "image_batch.h"
#include "pipeline_config.pb-c.h"
#include "cost_store.h"
#include "utils/minitrace.h"
#include "battery_simulator.h"
#include "sched_policy.h"
#include "dipp_process.h"
#include "cost_model.h"

static int param_of(Module *m, EffortLevel e)
{
    return e == EFFORT_LEVEL__LOW ? m->low_effort_param_id
         : e == EFFORT_LEVEL__MEDIUM ? m->medium_effort_param_id
         : m->high_effort_param_id;
}

/* Feasibility-preserving acceptance (sched_policy.h, accept = FEAS): an effort
   above the lowest is taken only if the frame's planned path (this module at
   that effort, the rest at lowest) stays within 1/k of its deadline window and
   every queued frame that was feasible at lowest effort stays feasible. The
   lowest level is always acceptable latency-wise; energy is checked for every
   level as before (judge_implementation). Until every later module has run once
   since boot there is no estimate of the rest of the path, and only the lowest
   level is taken.

   With sched_energy on, the feasibility walk also integrates the battery and
   adds recharge time (SchedEnergy), and an effort that is feasible that way but
   cannot be paid for now is WAITED for (NOT_FOUND: the batch halts and is
   retried, as at the floor) instead of stepping down to a lower effort. */
static int energy_now(SchedEnergy *en)
{
    double t_s, pay, orb;
    if (battery_simulator_energy_state(&en->w_wh, &en->floor_wh, &en->p_idle_w, &en->hold_s, &en->always, &t_s,
                                       &pay, &orb) != 0)
        return 0;
    en->p_s_w = sched_power_available_w();
    return 1;
}

static COST_MODEL_LOOKUP_RESULT feas_choose(Module *module, ImageBatch *data, size_t num_modules, int64_t now_us,
                                            float energy_requirement, EffortLevel lowest, int *module_param_id,
                                            uint32_t *picked_hash)
{
    static SchedFrame others[SCHED_MAX_QUEUED];
    EffortLevel order[3];
    int n_order = 0;
    if (sched_current_tier == SCHED_TIER_HIGH)
        order[n_order++] = EFFORT_LEVEL__HIGH;
    order[n_order++] = EFFORT_LEVEL__MEDIUM;
    order[n_order++] = EFFORT_LEVEL__LOW;
    uint32_t idx = (uint32_t)(data->progress + 1);
    int64_t rest = sched_rest_low_us(idx + 1, (uint32_t)num_modules, 1);
    size_t n_others = (size_t)-1;
    SchedEnergy en;
    int use_en = sched_get_energy() && energy_now(&en);
    for (int i = 0; i < n_order; i++)
    {
        EffortLevel e = order[i];
        int pid = param_of(module, e);
        if (pid == -1)
            continue;
        if (e == lowest)
            return judge_implementation(e, module, data, INT64_MAX, energy_requirement, module_param_id, picked_hash, true);
        if (rest < 0)
            continue;
        uint32_t lat;
        float e_uwh;
        cost_model_predict(&module_parameter_lists[pid], data->num_images, &lat, &e_uwh);
        if (n_others == (size_t)-1)
            n_others = sched_collect_queued(data->uuid, others, SCHED_MAX_QUEUED);
        SchedFrame cur = {data->priority, data->capture_us, (int64_t)lat + rest,
                          (double)e_uwh + sched_rest_low_uwh(idx + 1, (uint32_t)num_modules)};
        if (!sched_feasible(now_us, &cur, others, n_others, sched_get_k_x10(), use_en ? &en : NULL))
            continue;
        COST_MODEL_LOOKUP_RESULT r = judge_implementation(e, module, data, INT64_MAX, energy_requirement,
                                                          module_param_id, picked_hash, false);
        if (r == FOUND_NOT_CACHED || r == FOUND_CACHED)
            return r;
        if (use_en)
        {
            /* feasible including the recharge, not affordable now: wait for it */
            printf("SCHED_ENERGY_WAIT effort=%d need_uwh=%.1f battery_wh=%.4f floor_wh=%.4f p_s_w=%.3f\n", (int)e,
                   (double)e_uwh, en.w_wh, en.floor_wh, en.p_s_w);
            return NOT_FOUND;
        }
    }
    return NOT_FOUND;
}

COST_MODEL_LOOKUP_RESULT get_best_effort_implementation_config(Module *module, ImageBatch *data, size_t num_modules, int *module_param_id, uint32_t *picked_hash)
{
    MTR_BEGIN_FUNC();
    struct timespec time;
    if (clock_gettime(CLOCK_MONOTONIC, &time) < 0)
    {
        printf("Error getting time\n");
        MTR_END_FUNC();
        return NOT_FOUND;
    }

    size_t num_modules_left = num_modules - (data->progress + 1); // number of modules left to process

    /* Time left until the batch deadline, split evenly across the remaining
       modules. Signed: a passed deadline gives a negative budget, which the
       lowest-effort bypass then overrides. The previous version computed this
       in whole seconds and cast to uint32_t, so on AArch64 fcvtzu SATURATED a
       missed deadline to 0 -- which is why the reported "25 violations" carried
       no magnitude. */
    int64_t now_us = (int64_t)time.tv_sec * 1000000 + (int64_t)time.tv_nsec / 1000;
    int64_t latency_requirement = (data->priority - now_us) / (int64_t)num_modules_left;
    float battery_level_wh = get_battery_level_wh();
    float energy_requirement = (battery_level_wh - battery_safety_margin_wh) * 1000000.0f; // current battery level minus safety margin (microwatt-hours)

    MTR_COUNTER("main", "battery_level_uwh", (int)(battery_level_wh * 1000000.0f));

    MTR_INSTANT_I(__FILE__, "best_effort_heuristic", "latency_requirement (us)", (int)latency_requirement);
    MTR_INSTANT_I(__FILE__, "best_effort_heuristic", "energy_requirement (uWh)", (int)energy_requirement);

    // printf("Number of modules left: %zu, Latency requirement: %u, Energy requirement: %f\n", num_modules_left, latency_requirement, energy_requirement);

    if (module->default_effort_param_id != -1)
    {
        MTR_END_FUNC();
        return get_default_implementation(module, data, latency_requirement, energy_requirement, module_param_id, picked_hash);
    }
    else
    {
        EffortLevel lowest_effort_level = EFFORT_LEVEL__HIGH;
        if (module->medium_effort_param_id != -1)
        {
            lowest_effort_level = EFFORT_LEVEL__MEDIUM;
        }
        if (module->low_effort_param_id != -1)
        {
            lowest_effort_level = EFFORT_LEVEL__LOW;
        }

        if (sched_get_accept() == SCHED_ACCEPT_FEAS)
        {
            MTR_END_FUNC();
            return feas_choose(module, data, num_modules, now_us, energy_requirement, lowest_effort_level,
                               module_param_id, picked_hash);
        }

        COST_MODEL_LOOKUP_RESULT result;
        /* Medium tier (queue depth in [q_med, q_low), sched_policy.h): the
           search starts at medium and both remaining levels are considered
           without the latency gates -- medium if it fits the budget, else
           low, which as the lowest level bypasses the latency check. */
        if (sched_current_tier == SCHED_TIER_MEDIUM &&
            (module->medium_effort_param_id != -1 || module->low_effort_param_id != -1))
        {
            if (module->medium_effort_param_id != -1)
            {
                result = judge_implementation(EFFORT_LEVEL__MEDIUM, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__MEDIUM);
                if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
                {
                    MTR_END_FUNC();
                    return result;
                }
            }
            if (module->low_effort_param_id != -1)
                result = judge_implementation(EFFORT_LEVEL__LOW, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, true);
            else
                result = NOT_FOUND;
            MTR_END_FUNC();
            return result;
        }

        // start from the heavy and go down in the effort levels
        // (the first that fulfils the requirements is the one to use)
        result = judge_implementation(EFFORT_LEVEL__HIGH, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__HIGH);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            MTR_END_FUNC();
            return result;
        }
        // only check medium if we already considered high and latency requirement is tight enough
        if (module->high_effort_param_id != -1 && latency_requirement < sched_get_gate_med_us())
        {
            result = judge_implementation(EFFORT_LEVEL__MEDIUM, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__MEDIUM);
            if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
            {
                MTR_END_FUNC();
                return result;
            }
        }
        // only check low if we already considered higher efforts and latency requirement is tight enough
        if ((module->high_effort_param_id != -1 || module->medium_effort_param_id != -1) && latency_requirement < sched_get_gate_low_us())
        {
            result = judge_implementation(EFFORT_LEVEL__LOW, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__LOW);
            if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
            {
                MTR_END_FUNC();
                return result;
            }
        }
        // The effort levels are either empty or none of them fulfill the requirements
        MTR_END_FUNC();
        return NOT_FOUND;
    }
}

Heuristic best_effort_heuristic = {
    .name = "BEST_EFFORT",
    .heuristic_function = get_best_effort_implementation_config,
};