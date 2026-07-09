/*
 * gnb_message_handlers.c
 *
 * gNB-emulator (gNBemu) side handler for the custom RAN Service Model
 * defined in ran_messages.proto. Simulates a fixed-size fleet of UEs,
 * generates PHY/MAC samples correlated with RSRP, and answers
 * indication/control requests coming from the xApp over UDP.
 *
 * State is encapsulated in a single struct, sample generation is split
 * into small named helper functions, and no code path aborts the
 * process on malformed or unexpected input coming from the network.
 */

#include "gnb_message_handlers.h"
#include "ran_messages.pb-c.h"

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

/* ------------------------------------------------------------------- */
/* Configuration constants                                              */
/* ------------------------------------------------------------------- */

#define MAX_CONNECTED_UES 100      /* size of the simulated UE fleet */
#define GNB_MAX_PRB        217     /* 80 MHz / 30 kHz SCS, single numerology */

/* RSRP tiers used to correlate BER/MCS samples with signal quality. */
#define RSRP_STRONG_THRESHOLD_DBM (-80.0f)
#define RSRP_MEDIUM_THRESHOLD_DBM (-95.0f)
#define BER_UL_CAP                0.3f

/* ------------------------------------------------------------------- */
/* Internal state                                                       */
/* ------------------------------------------------------------------- */

/*
 * Shadow control-plane state for one simulated UE: only the fields the
 * xApp can actually write back (via RAN_control_request) live here, the
 * PHY/MAC measurements are re-sampled on every indication instead of
 * being persisted.
 */
typedef struct {
    int32_t rnti;
    bool aux_flag;
    float aux_value;      /* NAN-like sentinel: unset until first write */
    bool aux_value_is_set;
} ue_runtime_state_t;

typedef struct {
    ue_runtime_state_t ues[MAX_CONNECTED_UES];
    int32_t gnb_id;
    int32_t max_prb;
    int32_t last_allocated_prb;
    bool initialized;
} gnb_state_t;

static gnb_state_t g_gnb_state = {
    .gnb_id = 0,
    .max_prb = GNB_MAX_PRB,
    .last_allocated_prb = 0,
    .initialized = false,
};

static void gnb_state_init_if_needed(void) {
    if (g_gnb_state.initialized) {
        return;
    }
    for (int i = 0; i < MAX_CONNECTED_UES; i++) {
        g_gnb_state.ues[i].rnti = rand();
        g_gnb_state.ues[i].aux_flag = false;
        g_gnb_state.ues[i].aux_value_is_set = false;
    }
    g_gnb_state.initialized = true;
}

void gnb_state_reset(void) {
    g_gnb_state.initialized = false;
    gnb_state_init_if_needed();
}

/* ------------------------------------------------------------------- */
/* PHY/MAC sample generation (RSRP -> BER/MCS correlated randomization)  */
/* ------------------------------------------------------------------- */

typedef enum {
    LINK_QUALITY_STRONG,   /* RSRP >= -80 dBm  -> 256-QAM range   */
    LINK_QUALITY_MEDIUM,   /* -95 <= RSRP < -80 -> 64-QAM range   */
    LINK_QUALITY_WEAK,     /* RSRP < -95        -> QPSK/16-QAM    */
} link_quality_t;

static link_quality_t classify_link_quality(float rsrp_dbm) {
    if (rsrp_dbm >= RSRP_STRONG_THRESHOLD_DBM) {
        return LINK_QUALITY_STRONG;
    }
    if (rsrp_dbm >= RSRP_MEDIUM_THRESHOLD_DBM) {
        return LINK_QUALITY_MEDIUM;
    }
    return LINK_QUALITY_WEAK;
}

/* RSRP: -65 to -109 dBm, representative of a N78-band deployment. */
static float sample_rsrp_dbm(void) {
    return -65.0f - (float)(rand() % 45);
}

/*
 * Downlink/uplink BER, correlated with link quality. Uplink is always
 * worse than downlink (UEs transmit with less power than the gNB) and is
 * capped at BER_UL_CAP: past that point a real UE would have dropped the
 * radio link already.
 */
static void sample_ber(link_quality_t quality, float *ber_ul, float *ber_dl) {
    switch (quality) {
        case LINK_QUALITY_STRONG:
            *ber_dl = (rand() % 5) / 1000.0f;                          /* 0.000-0.004 */
            *ber_ul = *ber_dl + 0.001f + (rand() % 5) / 1000.0f;       /* +0.001-0.005 */
            break;
        case LINK_QUALITY_MEDIUM:
            *ber_dl = (rand() % 50) / 1000.0f;                         /* 0.000-0.049 */
            *ber_ul = *ber_dl + 0.005f + (rand() % 20) / 1000.0f;      /* +0.005-0.025 */
            break;
        case LINK_QUALITY_WEAK:
        default:
            *ber_dl = (rand() % 200) / 1000.0f;                        /* 0.000-0.199 */
            *ber_ul = *ber_dl + 0.01f + (rand() % 50) / 1000.0f;       /* +0.010-0.059 */
            break;
    }
    if (*ber_ul > BER_UL_CAP) {
        *ber_ul = BER_UL_CAP;
    }
}

/* MCS index, correlated with link quality: 0-9 (QPSK/16-QAM), 10-19
 * (64-QAM), 20-27 (256-QAM). */
static void sample_mcs(link_quality_t quality, uint32_t *mcs_ul, uint32_t *mcs_dl) {
    switch (quality) {
        case LINK_QUALITY_STRONG:
            *mcs_ul = 20 + rand() % 8;
            *mcs_dl = 20 + rand() % 8;
            break;
        case LINK_QUALITY_MEDIUM:
            *mcs_ul = 10 + rand() % 10;
            *mcs_dl = 10 + rand() % 10;
            break;
        case LINK_QUALITY_WEAK:
        default:
            *mcs_ul = rand() % 10;
            *mcs_dl = rand() % 10;
            break;
    }
}

/*
 * A more accurate model would derive an SNR estimate from RSRP, pick the
 * MCS via a link-adaptation table, and compute the resulting BER via the
 * Q-function; here BER and MCS are both sampled independently from the
 * same RSRP tier, which is good enough to exercise the xApp end to end.
 */
static UeInfo *build_ue_info_message(const ue_runtime_state_t *ue) {
    UeInfo *info = malloc(sizeof(UeInfo));
    if (!info) {
        return NULL;
    }
    ue_info__init(info);

    info->rnti = (uint32_t)ue->rnti;

    float rsrp_dbm = sample_rsrp_dbm();
    link_quality_t quality = classify_link_quality(rsrp_dbm);

    float ber_ul, ber_dl;
    sample_ber(quality, &ber_ul, &ber_dl);

    uint32_t mcs_ul, mcs_dl;
    sample_mcs(quality, &mcs_ul, &mcs_dl);

    info->has_rsrp_dbm = 1;
    info->rsrp_dbm = rsrp_dbm;
    info->has_ber_ul = 1;
    info->ber_ul = ber_ul;
    info->has_ber_dl = 1;
    info->ber_dl = ber_dl;
    info->has_mcs_ul = 1;
    info->mcs_ul = mcs_ul;
    info->has_mcs_dl = 1;
    info->mcs_dl = mcs_dl;

    info->has_aux_flag = 1;
    info->aux_flag = ue->aux_flag;
    if (ue->aux_value_is_set) {
        info->has_aux_value = 1;
        info->aux_value = ue->aux_value;
    }

    return info;
}

/* Builds a fresh UeList snapshot for every currently-simulated UE. The
 * caller owns the returned message and must free it with
 * free_ue_list_message(). */
static UeList *build_ue_list_message(void) {
    UeList *ue_list = malloc(sizeof(UeList));
    if (!ue_list) {
        return NULL;
    }
    ue_list__init(ue_list);

    ue_list->connected_ues = MAX_CONNECTED_UES;
    ue_list->n_ue_info = MAX_CONNECTED_UES;
    ue_list->ue_info = calloc(MAX_CONNECTED_UES, sizeof(UeInfo *));
    if (!ue_list->ue_info) {
        free(ue_list);
        return NULL;
    }

    for (int i = 0; i < MAX_CONNECTED_UES; i++) {
        ue_list->ue_info[i] = build_ue_info_message(&g_gnb_state.ues[i]);
    }

    return ue_list;
}

static void free_ue_list_message(UeList *ue_list) {
    if (!ue_list) {
        return;
    }
    for (size_t i = 0; i < ue_list->n_ue_info; i++) {
        free(ue_list->ue_info[i]);
        ue_list->ue_info[i] = NULL;
    }
    free(ue_list->ue_info);
    ue_list->ue_info = NULL;
    free(ue_list);
}

/* ------------------------------------------------------------------- */
/* Parameter map read/write helpers                                      */
/* ------------------------------------------------------------------- */

static const char *ran_parameter_name(RanParameterId param_id) {
    switch (param_id) {
        case RAN_PARAMETER_ID__GNB_ID:           return "gnb_id";
        case RAN_PARAMETER_ID__UE_LIST:          return "ue_list";
        case RAN_PARAMETER_ID__GLOBAL_PRB_ALLOC: return "global_prb_alloc";
        case RAN_PARAMETER_ID__MAX_PRB:          return "max_prb";
        default:                                 return "unrecognized_param";
    }
}

static char *int32_to_new_string(int32_t value) {
    int length = snprintf(NULL, 0, "%d", value) + 1;
    char *str = malloc((size_t)length);
    if (str) {
        snprintf(str, (size_t)length, "%d", value);
    }
    return str;
}

/* Samples a new global PRB allocation, proportional to the simulated UE
 * fleet size and capped at the gNB's max PRB count. */
static int32_t sample_global_prb_alloc(void) {
    int32_t sample = rand() % ((g_gnb_state.max_prb / 2) + (2 * MAX_CONNECTED_UES));
    if (sample > g_gnb_state.max_prb) {
        sample = g_gnb_state.max_prb;
    }
    return sample;
}

/*
 * Populates `entry->key` (already set by the caller) with the current
 * value of that RAN parameter. Returns false for unrecognized/unsupported
 * parameters instead of aborting: a malformed or unexpected request from
 * the network must never crash the gNB emulator.
 */
static bool ran_read_parameter(RanParameterMapEntry *entry) {
    switch (entry->key) {
        case RAN_PARAMETER_ID__GNB_ID:
            entry->value_case = RAN_PARAMETER_MAP_ENTRY__VALUE_STRING_VALUE;
            entry->string_value = int32_to_new_string(g_gnb_state.gnb_id);
            return entry->string_value != NULL;

        case RAN_PARAMETER_ID__UE_LIST:
            entry->value_case = RAN_PARAMETER_MAP_ENTRY__VALUE_UE_LIST;
            entry->ue_list = build_ue_list_message();
            return entry->ue_list != NULL;

        case RAN_PARAMETER_ID__GLOBAL_PRB_ALLOC:
            g_gnb_state.last_allocated_prb = sample_global_prb_alloc();
            entry->value_case = RAN_PARAMETER_MAP_ENTRY__VALUE_INT64_VALUE;
            entry->int64_value = g_gnb_state.last_allocated_prb;
            return true;

        case RAN_PARAMETER_ID__MAX_PRB:
            entry->value_case = RAN_PARAMETER_MAP_ENTRY__VALUE_INT64_VALUE;
            entry->int64_value = g_gnb_state.max_prb;
            return true;

        default:
            fprintf(stderr, "ran_read_parameter: unsupported parameter id %d\n", entry->key);
            return false;
    }
}

/* Looks up the simulated UE with the given RNTI and applies the
 * control properties carried by `source`. Returns false if not found. */
static bool apply_ue_control_properties(int32_t rnti, const UeInfo *source) {
    for (int i = 0; i < MAX_CONNECTED_UES; i++) {
        if (g_gnb_state.ues[i].rnti != rnti) {
            continue;
        }
        g_gnb_state.ues[i].aux_flag = source->has_aux_flag ? source->aux_flag : g_gnb_state.ues[i].aux_flag;
        if (source->has_aux_value) {
            g_gnb_state.ues[i].aux_value = source->aux_value;
            g_gnb_state.ues[i].aux_value_is_set = true;
        }
        return true;
    }
    return false;
}

static void ran_write_ue_list(const UeList *ue_list) {
    for (size_t i = 0; i < ue_list->n_ue_info; i++) {
        const UeInfo *incoming = ue_list->ue_info[i];
        if (!apply_ue_control_properties(incoming->rnti, incoming)) {
            fprintf(stderr, "ran_write_ue_list: RNTI %d not found\n", incoming->rnti);
        }
    }
}

static void ran_write_parameter(const RanParameterMapEntry *entry) {
    switch (entry->key) {
        case RAN_PARAMETER_ID__GNB_ID:
            g_gnb_state.gnb_id = atoi(entry->string_value);
            break;
        case RAN_PARAMETER_ID__UE_LIST:
            ran_write_ue_list(entry->ue_list);
            break;
        default:
            fprintf(stderr, "ran_write_parameter: cannot write unrecognized parameter %d\n", entry->key);
    }
}

/* ------------------------------------------------------------------- */
/* RanMessage handlers                                                  */
/* ------------------------------------------------------------------- */

static void handle_subscription(RanMessage *request) {
    fprintf(stderr, "handle_subscription: not implemented in this PoC\n");
    ran_message__free_unpacked(request, NULL);
}

static void send_indication_response(const RanIndicationRequest *request, int out_socket,
                                      struct sockaddr_in servaddr) {
    RanIndicationResponse response = RAN_INDICATION_RESPONSE__INIT;
    size_t param_count = request->n_target_params;

    RanParameterMapEntry **entries = malloc(sizeof(RanParameterMapEntry *) * param_count);
    if (!entries) {
        fprintf(stderr, "send_indication_response: out of memory building %zu entries\n", param_count);
        return;
    }

    for (size_t i = 0; i < param_count; i++) {
        entries[i] = malloc(sizeof(RanParameterMapEntry));
        ran_parameter_map_entry__init(entries[i]);
        entries[i]->key = request->target_params[i];
        ran_read_parameter(entries[i]);
    }

    response.n_param_map = param_count;
    response.param_map = entries;

    size_t buflen = ran_indication_response__get_packed_size(&response);
    uint8_t *buf = malloc(buflen);
    if (!buf) {
        fprintf(stderr, "send_indication_response: out of memory packing response\n");
    } else {
        ran_indication_response__pack(&response, buf);
        ssize_t sent = sendto(out_socket, buf, buflen, MSG_CONFIRM,
                               (const struct sockaddr *)&servaddr, sizeof(servaddr));
        fprintf(stderr, "send_indication_response: sent %zd/%zu bytes\n", sent, buflen);
        free(buf);
    }

    for (size_t i = 0; i < param_count; i++) {
        if (entries[i]->value_case == RAN_PARAMETER_MAP_ENTRY__VALUE_STRING_VALUE) {
            free(entries[i]->string_value);
        } else if (entries[i]->value_case == RAN_PARAMETER_MAP_ENTRY__VALUE_UE_LIST) {
            free_ue_list_message(entries[i]->ue_list);
        }
        free(entries[i]);
    }
    free(entries);
}

static void handle_indication_request(RanMessage *request, int out_socket, struct sockaddr_in servaddr) {
    const RanIndicationRequest *indication_request = request->indication_request;

    fprintf(stderr, "handle_indication_request: %zu parameter(s) requested:\n",
            indication_request->n_target_params);
    for (size_t i = 0; i < indication_request->n_target_params; i++) {
        RanParameterId param_id = indication_request->target_params[i];
        fprintf(stderr, "  - parameter %d (%s)\n", param_id, ran_parameter_name(param_id));
    }

    send_indication_response(indication_request, out_socket, servaddr);
    ran_message__free_unpacked(request, NULL);
}

static void handle_control(RanMessage *request) {
    const RanControlRequest *control_request = request->control_request;

    for (size_t i = 0; i < control_request->n_target_param_map; i++) {
        const RanParameterMapEntry *entry = control_request->target_param_map[i];
        fprintf(stderr, "handle_control: applying parameter %s\n", ran_parameter_name(entry->key));
        ran_write_parameter(entry);
    }
    ran_message__free_unpacked(request, NULL);
}

/* ------------------------------------------------------------------- */
/* Public entry point                                                    */
/* ------------------------------------------------------------------- */

void handle_master_message(void *buf, int buflen, int out_socket, struct sockaddr_in servaddr) {
    gnb_state_init_if_needed();

    RanMessage *request = ran_message__unpack(NULL, (size_t)buflen, buf);
    if (!request) {
        fprintf(stderr, "handle_master_message: failed to decode %d-byte message, raw bytes:\n", buflen);
        for (int i = 0; i < buflen; i++) {
            fprintf(stderr, " %02hhx", ((uint8_t *)buf)[i]);
        }
        fprintf(stderr, "\n");
        return;
    }

    switch (request->msg_type) {
        case RAN_MESSAGE_TYPE__SUBSCRIPTION:
            handle_subscription(request);
            break;
        case RAN_MESSAGE_TYPE__INDICATION_REQUEST:
            handle_indication_request(request, out_socket, servaddr);
            break;
        case RAN_MESSAGE_TYPE__CONTROL:
            handle_control(request);
            break;
        case RAN_MESSAGE_TYPE__INDICATION_RESPONSE:
            /* The gNB emulator never receives an indication *response*
             * (it only ever sends them); log and drop defensively. */
            fprintf(stderr, "handle_master_message: unexpected INDICATION_RESPONSE, dropping\n");
            ran_message__free_unpacked(request, NULL);
            break;
        default:
            fprintf(stderr, "handle_master_message: unrecognized message type %d\n", request->msg_type);
            ran_message__free_unpacked(request, NULL);
            break;
    }
}
