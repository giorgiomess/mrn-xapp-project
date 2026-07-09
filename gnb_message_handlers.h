/*
 * gnb_message_handlers.h
 *
 * Public interface of the gNB-emulator side handler for the custom RAN
 * Service Model defined in ran_messages.proto. The socket/dispatch loop
 * (main.c, not part of this deliverable) only needs to know about
 * handle_master_message(): every other symbol in gnb_message_handlers.c
 * is internal (static) to that translation unit.
 */

#ifndef GNB_MESSAGE_HANDLERS_H
#define GNB_MESSAGE_HANDLERS_H

#include <netinet/in.h>

/*
 * Decodes a single RanMessage received on `buf` (`buflen` bytes) and
 * dispatches it to the matching handler (SUBSCRIPTION / INDICATION_REQUEST /
 * CONTROL). Replies, when applicable, are sent on `out_socket` to `servaddr`.
 * Ownership of `buf` stays with the caller.
 */
void handle_master_message(void *buf, int buflen, int out_socket,
                            struct sockaddr_in servaddr);

/*
 * Resets the internal gNB emulator state (UE fleet, PRB counters). Exposed
 * mainly for test harnesses that need a clean state between test cases.
 */
void gnb_state_reset(void);

#endif /* GNB_MESSAGE_HANDLERS_H */
