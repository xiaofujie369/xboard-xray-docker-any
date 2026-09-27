// SPDX-License-Identifier: GPL-3.0-or-later
// XBoard session limiter extension for sing-box v1.12.25.
package option

type UserLimitValues struct {
	TCP   int `json:"max_tcp,omitempty"`
	UDP   int `json:"max_udp,omitempty"`
	Total int `json:"max_total,omitempty"`
	Rate  int `json:"new_per_second,omitempty"`
	Burst int `json:"burst,omitempty"`
}

type UserLimitsOptions struct {
	Scope    string                     `json:"scope,omitempty"`
	Defaults UserLimitValues            `json:"defaults"`
	Users    map[string]UserLimitValues `json:"users,omitempty"`
}
