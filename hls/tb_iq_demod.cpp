// Testbench: replays the bit-exact vectors produced by model/gen_vectors.py and compares sample by sample.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include "iq_demod.h"

struct Case {
    std::string name;
    unsigned regs[12];  // ftw lpf_m_sig lpf_m_ref notch_en notch_coef out_sel out_gain i_offset q_offset rot_cos rot_sin (+rst)
};

static bool read_regs(const std::string &path, unsigned *r) {
    std::ifstream f(path.c_str());
    if (!f) return false;
    std::string name;
    unsigned long long v;
    int n = 0;
    while (f >> name >> v && n < 11) r[n++] = (unsigned)v;
    return n == 11;
}

static int run_case(const std::string &name, long nmax) {
    unsigned r[12];
    if (!read_regs("tb_data/" + name + "_regs.txt", r)) {
        std::cerr << "cannot read regs for " << name << "\n";
        return 1;
    }
    std::ifstream fin(("tb_data/" + name + "_in.txt").c_str());
    std::ifstream fexp(("tb_data/" + name + "_out.txt").c_str());
    if (!fin || !fexp) {
        std::cerr << "cannot open vectors for " << name << "\n";
        return 1;
    }
    hls::stream<axis_t> adc_in("adc_in"), dac_out("dac_out");
    int m1 = 0, m2 = 0, m3 = 0, m4 = 0, m5 = 0, m6 = 0; unsigned mc = 0;

    // Two samples with rst_state = 1 to clear the static state (outputs discarded).
    // Not in cosim (nmax > 0): the RTL powers up with zeroed state and the cosim harness cannot
    // change AXI-Lite registers mid-stream for an ap_ctrl_none design, so cosim runs one case
    // with constant registers and no reset.
    for (int k = 0; k < 2 && nmax == 0; k++) {
        axis_t x; x.data = 0; x.keep = 0xF; x.strb = 0xF; x.last = 0; x.user = 0; x.id = 0; x.dest = 0;
        adc_in.write(x);
        iq_demod(adc_in, dac_out, r[0], r[1], r[2], r[3], (int)r[4], r[5], (int)r[6], (int)r[7], (int)r[8], (short)r[9], (short)r[10], 1, 16, &m1, &m2, &m3, &m4, &m5, &m6, &mc);
        dac_out.read();
    }

    long n = 0, errors = 0, maxerr = 0;
    long in1, in2, e1, e2;
    while (fin >> in1 >> in2 && fexp >> e1 >> e2) {
        if (nmax > 0 && n >= nmax) break;
        axis_t x;
        x.data.range(15, 0) = ap_int<16>(in1);
        x.data.range(31, 16) = ap_int<16>(in2);
        x.keep = 0xF; x.strb = 0xF; x.last = 0; x.user = 0; x.id = 0; x.dest = 0;
        adc_in.write(x);
        iq_demod(adc_in, dac_out, r[0], r[1], r[2], r[3], (int)r[4], r[5], (int)r[6], (int)r[7], (int)r[8], (short)r[9], (short)r[10], 0, 16, &m1, &m2, &m3, &m4, &m5, &m6, &mc);
        axis_t y = dac_out.read();
        long o1 = (long)ap_int<16>(y.data.range(15, 0));
        long o2 = (long)ap_int<16>(y.data.range(31, 16));
        long d1 = labs(o1 - e1), d2 = labs(o2 - e2);
        if (d1 > maxerr) maxerr = d1;
        if (d2 > maxerr) maxerr = d2;
        if (d1 || d2) {
            if (errors < 10)
                std::printf("  [%s] sample %ld: got (%ld, %ld) expected (%ld, %ld)\n", name.c_str(), n, o1, o2, e1, e2);
            errors++;
        }
        n++;
    }
    std::printf("case %-10s : %ld samples, %ld mismatches, max |err| = %ld  -> %s\n",
                name.c_str(), n, errors, maxerr, errors ? "FAIL" : "PASS");
    return errors ? 1 : 0;
}

int main() {
    long nmax = 0;
    {
        std::ifstream f("tb_data/nmax.txt");
        if (f) f >> nmax;
    }
    // cosim (nmax > 0) may restrict the run to the cases listed in tb_data/cases_cosim.txt
    std::ifstream cases(nmax > 0 ? "tb_data/cases_cosim.txt" : "tb_data/cases.txt");
    if (!cases && nmax > 0) cases.open("tb_data/cases.txt");
    if (!cases) {
        std::cerr << "tb_data/cases.txt not found (run model/gen_vectors.py)\n";
        return 1;
    }
    int fails = 0;
    std::string name;
    while (cases >> name) fails += run_case(name, nmax);
    std::printf("%s\n", fails ? "*** TESTBENCH FAILED ***" : "*** ALL CASES PASS ***");
    return fails;
}
