// SPDX-License-Identifier: MIT
/*
 * Raw IPv4 stream adapter for libslirp.
 *
 * stdin and stdout use the same framing: a four-byte big-endian length
 * followed by one IPv4 packet.  No generated executable belongs in Git; the
 * Python launcher builds this source in the external runtime directory.
 */

#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <errno.h>
#include <limits>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <unordered_set>
#include <vector>

#include <slirp/libslirp.h>

enum { MAX_PACKET = 65'535, MAX_INPUT = 1'048'576, MAX_POLL_FDS = 1'024 };

struct bridge {
  struct timer {
    SlirpTimerCb callback;
    void *opaque;
    int64_t expires_ms = std::numeric_limits<int64_t>::max();
  };

  Slirp *slirp;
  struct pollfd poll_fds[MAX_POLL_FDS];
  size_t poll_count;
  uint8_t input[MAX_INPUT];
  size_t input_length;
  bool running;
  bool arp_announced;
  uint8_t arp_reply[42];
  bool arp_pending;
  std::unordered_set<timer *> timers;
};

static uint32_t read_be32(uint8_t const *data) {
  return (uint32_t(data[0]) << 24) | (uint32_t(data[1]) << 16) |
         (uint32_t(data[2]) << 8) | uint32_t(data[3]);
}

static uint16_t read_be16(uint8_t const *data) {
  return (uint16_t(data[0]) << 8) | data[1];
}

static void write_be16(uint8_t *data, uint16_t value) {
  data[0] = uint8_t(value >> 8);
  data[1] = uint8_t(value);
}

static void write_be32(uint8_t *data, uint32_t value) {
  data[0] = uint8_t(value >> 24);
  data[1] = uint8_t(value >> 16);
  data[2] = uint8_t(value >> 8);
  data[3] = uint8_t(value);
}

static bool write_all(int fd, void const *data, size_t length) {
  uint8_t const *cursor = static_cast<uint8_t const *>(data);
  while (length) {
    ssize_t const written = write(fd, cursor, length);
    if (written < 0) {
      if (errno == EINTR)
        continue;
      return false;
    }
    cursor += written;
    length -= size_t(written);
  }
  return true;
}

static uint16_t replace_checksum_word(uint16_t checksum, uint16_t old_word,
                                      uint16_t new_word) {
  uint32_t sum = uint16_t(~checksum) + uint16_t(~old_word) + new_word;
  sum = (sum & 0xffff) + (sum >> 16);
  sum = (sum & 0xffff) + (sum >> 16);
  return uint16_t(~sum);
}

static slirp_ssize_t send_packet(void const *packet, size_t length,
                                 void *opaque) {
  struct bridge *bridge = static_cast<struct bridge *>(opaque);
  uint8_t const *ethernet = static_cast<uint8_t const *>(packet);
  if (length < 14)
    return slirp_ssize_t(length);

  uint16_t const ether_type = (uint16_t(ethernet[12]) << 8) | ethernet[13];
  if (ether_type == 0x0806 && length >= sizeof(bridge->arp_reply) &&
      ethernet[20] == 0x00 && ethernet[21] == 0x01 &&
      !memcmp(ethernet + 38, "\x0a\x00\x02\x0f", 4)) {
    static uint8_t const guest_mac[6] = {0x52, 0x54, 0x00, 0x12, 0x34, 0x56};
    uint8_t *reply = bridge->arp_reply;
    memcpy(reply, ethernet + 6, 6);
    memcpy(reply + 6, guest_mac, sizeof(guest_mac));
    memcpy(reply + 12, ethernet + 12, 8);
    reply[20] = 0x00;
    reply[21] = 0x02;
    memcpy(reply + 22, guest_mac, sizeof(guest_mac));
    memcpy(reply + 28, ethernet + 38, 4);
    memcpy(reply + 32, ethernet + 22, 6);
    memcpy(reply + 38, ethernet + 28, 4);
    bridge->arp_pending = true;
    return slirp_ssize_t(length);
  }
  if (ether_type != 0x0800)
    return slirp_ssize_t(length);

  if (length < 34 || (ethernet[14] >> 4) != 4)
    return slirp_ssize_t(length);
  size_t const ip_length = (size_t(ethernet[16]) << 8) | ethernet[17];
  size_t const ip_header_length = size_t(ethernet[14] & 0x0f) * 4;
  if (ip_length < ip_header_length || ip_header_length < 20 ||
      ip_length > length - 14)
    return slirp_ssize_t(length);
  std::vector<uint8_t> ip(ethernet + 14, ethernet + 14 + ip_length);
  uint16_t const fragment = read_be16(ip.data() + 6);
  if (ip[9] == IPPROTO_TCP && (fragment & 0x1fff) == 0 &&
      ip_length >= ip_header_length + 20) {
    uint8_t *tcp = ip.data() + ip_header_length;
    uint16_t const window = read_be16(tcp + 14);
    constexpr uint16_t guest_compatible_window = 4'096;
    if (window > guest_compatible_window) {
      uint16_t const checksum = read_be16(tcp + 16);
      write_be16(tcp + 14, guest_compatible_window);
      write_be16(tcp + 16, replace_checksum_word(checksum, window,
                                                 guest_compatible_window));
    }
  }
  uint8_t header[4];
  write_be32(header, uint32_t(ip_length));
  if (!write_all(STDOUT_FILENO, header, sizeof(header)) ||
      !write_all(STDOUT_FILENO, ip.data(), ip_length))
    return -1;
  return slirp_ssize_t(length);
}

static void inject_pending_arp(struct bridge *bridge) {
  if (!bridge->arp_pending)
    return;
  bridge->arp_pending = false;
  slirp_input(bridge->slirp, bridge->arp_reply, sizeof(bridge->arp_reply));
}

static void guest_error(char const *message, void *opaque) {
  (void)opaque;
  fprintf(stderr, "libslirp guest error: %s\n", message);
}

static int64_t clock_get_ns(void *opaque) {
  (void)opaque;
  struct timespec now;
  if (clock_gettime(CLOCK_MONOTONIC, &now))
    return 0;
  return int64_t(now.tv_sec) * 1'000'000'000 + now.tv_nsec;
}

static void *timer_new(SlirpTimerCb callback, void *callback_opaque,
                       void *opaque) {
  struct bridge *bridge = static_cast<struct bridge *>(opaque);
  auto *timer = new bridge::timer{callback, callback_opaque};
  bridge->timers.emplace(timer);
  return timer;
}

static void timer_free(void *timer_opaque, void *opaque) {
  struct bridge *bridge = static_cast<struct bridge *>(opaque);
  auto *timer = static_cast<bridge::timer *>(timer_opaque);
  bridge->timers.erase(timer);
  delete timer;
}

static void timer_mod(void *timer_opaque, int64_t expires_ms, void *opaque) {
  (void)opaque;
  static_cast<bridge::timer *>(timer_opaque)->expires_ms = expires_ms;
}

static void pump_timers(struct bridge *bridge) {
  int64_t const now_ms = clock_get_ns(nullptr) / 1'000'000;
  std::vector<bridge::timer *> due;
  for (bridge::timer *timer : bridge->timers) {
    if (timer->expires_ms <= now_ms) {
      timer->expires_ms = std::numeric_limits<int64_t>::max();
      due.emplace_back(timer);
    }
  }
  for (bridge::timer *timer : due) {
    if (bridge->timers.find(timer) != bridge->timers.end())
      timer->callback(timer->opaque);
  }
}

static void notify(void *opaque) { (void)opaque; }

static void register_poll_socket(slirp_os_socket socket, void *opaque) {
  (void)socket;
  (void)opaque;
}

static void unregister_poll_socket(slirp_os_socket socket, void *opaque) {
  (void)socket;
  (void)opaque;
}

static short poll_events(int events) {
  short result = 0;
  if (events & SLIRP_POLL_IN)
    result |= POLLIN;
  if (events & SLIRP_POLL_OUT)
    result |= POLLOUT;
  if (events & SLIRP_POLL_PRI)
    result |= POLLPRI;
  if (events & SLIRP_POLL_ERR)
    result |= POLLERR;
  if (events & SLIRP_POLL_HUP)
    result |= POLLHUP;
  return result;
}

static int slirp_events(short events) {
  int result = 0;
  if (events & POLLIN)
    result |= SLIRP_POLL_IN;
  if (events & POLLOUT)
    result |= SLIRP_POLL_OUT;
  if (events & POLLPRI)
    result |= SLIRP_POLL_PRI;
  if (events & POLLERR)
    result |= SLIRP_POLL_ERR;
  if (events & POLLHUP)
    result |= SLIRP_POLL_HUP;
  return result;
}

static int add_poll_socket(slirp_os_socket fd, int events, void *opaque) {
  struct bridge *bridge = static_cast<struct bridge *>(opaque);
  if (bridge->poll_count >= MAX_POLL_FDS)
    return -1;
  size_t const index = bridge->poll_count++;
  bridge->poll_fds[index].fd = fd;
  bridge->poll_fds[index].events = poll_events(events);
  bridge->poll_fds[index].revents = 0;
  return int(index);
}

static int get_revents(int index, void *opaque) {
  struct bridge *bridge = static_cast<struct bridge *>(opaque);
  if (index < 0 || size_t(index) >= bridge->poll_count)
    return 0;
  return slirp_events(bridge->poll_fds[index].revents);
}

static bool inject_ipv4(struct bridge *bridge, uint8_t const *packet,
                        size_t length) {
  size_t const header_length =
      length ? size_t(packet[0] & 0x0f) * 4 : size_t(0);
  size_t const declared_length =
      length >= 4 ? (size_t(packet[2]) << 8) | packet[3] : size_t(0);
  if (length < 20 || length > MAX_PACKET || (packet[0] >> 4) != 4 ||
      header_length < 20 || declared_length != length) {
    fprintf(stderr, "invalid IPv4 input length %zu\n", length);
    return false;
  }

  uint8_t ethernet[MAX_PACKET + 14] = {};
  static uint8_t const destination[6] = {0x52, 0x55, 0x0a, 0x00, 0x02, 0x02};
  static uint8_t const source[6] = {0x52, 0x54, 0x00, 0x12, 0x34, 0x56};
  if (!bridge->arp_announced) {
    uint8_t arp[42] = {};
    memcpy(arp, destination, sizeof(destination));
    memcpy(arp + 6, source, sizeof(source));
    arp[12] = 0x08;
    arp[13] = 0x06;
    arp[14] = 0x00;
    arp[15] = 0x01;
    arp[16] = 0x08;
    arp[17] = 0x00;
    arp[18] = 0x06;
    arp[19] = 0x04;
    arp[20] = 0x00;
    arp[21] = 0x02;
    memcpy(arp + 22, source, sizeof(source));
    memcpy(arp + 28, "\x0a\x00\x02\x0f", 4);
    memcpy(arp + 32, destination, sizeof(destination));
    memcpy(arp + 38, "\x0a\x00\x02\x02", 4);
    slirp_input(bridge->slirp, arp, sizeof(arp));
    bridge->arp_announced = true;
  }

  memcpy(ethernet, destination, sizeof(destination));
  memcpy(ethernet + 6, source, sizeof(source));
  ethernet[12] = 0x08;
  ethernet[13] = 0x00;
  memcpy(ethernet + 14, packet, length);
  slirp_input(bridge->slirp, ethernet,
              int(length + 14 < 60 ? 60 : length + 14));
  return true;
}

static bool consume_input(struct bridge *bridge) {
  while (bridge->input_length >= 4) {
    uint32_t const length = read_be32(bridge->input);
    if (!length || length > MAX_PACKET) {
      fprintf(stderr, "invalid framed packet length %u\n", length);
      return false;
    }
    if (bridge->input_length < size_t(length) + 4)
      break;
    if (!inject_ipv4(bridge, bridge->input + 4, length))
      return false;
    size_t const consumed = size_t(length) + 4;
    memmove(bridge->input, bridge->input + consumed,
            bridge->input_length - consumed);
    bridge->input_length -= consumed;
  }
  return true;
}

static bool read_input(struct bridge *bridge) {
  if (bridge->input_length >= sizeof(bridge->input)) {
    fputs("input buffer exhausted\n", stderr);
    return false;
  }
  ssize_t const received =
      read(STDIN_FILENO, bridge->input + bridge->input_length,
           sizeof(bridge->input) - bridge->input_length);
  if (received < 0) {
    if (errno == EINTR || errno == EAGAIN)
      return true;
    perror("read");
    return false;
  }
  if (!received) {
    bridge->running = false;
    return true;
  }
  bridge->input_length += size_t(received);
  return consume_input(bridge);
}

static bool parse_address(char const *text, struct in_addr *address) {
  if (inet_pton(AF_INET, text, address) == 1)
    return true;
  fprintf(stderr, "invalid IPv4 configuration address: %s\n", text);
  return false;
}

int main(int argc, char **argv) {
  bool allow_host_loopback = false;
  for (int index = 1; index < argc; ++index) {
    if (!strcmp(argv[index], "--allow-host-loopback"))
      allow_host_loopback = true;
    else {
      fprintf(stderr, "usage: %s [--allow-host-loopback]\n", argv[0]);
      return 2;
    }
  }

  struct SlirpConfig config = {};
  config.version = SLIRP_CONFIG_VERSION_MAX;
  config.restricted = 0;
  config.in_enabled = true;
  config.in6_enabled = false;
  config.vprefix_len = 0;
  config.vhostname = "magic-cap";
  config.if_mtu = 576;
  config.if_mru = 1'500;
  config.disable_host_loopback = !allow_host_loopback;
  if (!parse_address("10.0.2.0", &config.vnetwork) ||
      !parse_address("255.255.255.0", &config.vnetmask) ||
      !parse_address("10.0.2.2", &config.vhost) ||
      !parse_address("10.0.2.15", &config.vdhcp_start) ||
      !parse_address("10.0.2.3", &config.vnameserver))
    return 2;

  struct SlirpCb callbacks = {};
  callbacks.send_packet = send_packet;
  callbacks.guest_error = guest_error;
  callbacks.clock_get_ns = clock_get_ns;
  callbacks.timer_new = timer_new;
  callbacks.timer_free = timer_free;
  callbacks.timer_mod = timer_mod;
  callbacks.notify = notify;
  callbacks.register_poll_socket = register_poll_socket;
  callbacks.unregister_poll_socket = unregister_poll_socket;

  struct bridge bridge = {};
  bridge.running = true;
  bridge.slirp = slirp_new(&config, &callbacks, &bridge);
  if (!bridge.slirp) {
    fputs("unable to initialize libslirp\n", stderr);
    return 1;
  }

  while (bridge.running) {
    pump_timers(&bridge);
    bridge.poll_count = 1;
    bridge.poll_fds[0].fd = STDIN_FILENO;
    bridge.poll_fds[0].events = POLLIN;
    bridge.poll_fds[0].revents = 0;
    uint32_t timeout = 100;
    slirp_pollfds_fill_socket(bridge.slirp, &timeout, add_poll_socket, &bridge);
    int const ready = poll(bridge.poll_fds, nfds_t(bridge.poll_count),
                           timeout == UINT32_MAX ? -1 : int(timeout));
    if (ready < 0 && errno != EINTR) {
      perror("poll");
      break;
    }
    if (ready > 0 && (bridge.poll_fds[0].revents & (POLLIN | POLLHUP))) {
      if (!read_input(&bridge))
        break;
      inject_pending_arp(&bridge);
      if (!bridge.running)
        break;
      continue;
    }
    slirp_pollfds_poll(bridge.slirp, ready < 0, get_revents, &bridge);
    inject_pending_arp(&bridge);
    pump_timers(&bridge);
  }

  slirp_cleanup(bridge.slirp);
  for (bridge::timer *timer : bridge.timers)
    delete timer;
  return bridge.running ? 1 : 0;
}
