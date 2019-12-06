# MIT License
#
# Copyright (c) 2018-2019 Tskit Developers
# Copyright (C) 2016 University of Oxford
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
Test cases for ld matrix calculations.
"""
import unittest
import io

import numpy as np
import msprime

import tskit
import _tskit
#import tests.tsutil as tsutil
#import tests.test_wright_fisher as wf


##############################
# LD matrix calculator
##############################


def naive_ld_matrix(ts, sample_sets, function, windows=None):
    ns = [len(ss) for ss in sample_sets]
    # computes statistics based on genotypes of mutations
    if windows is None:
        windows = [0, ts.sequence_length]
    windows = np.array(windows)
    
    num_window_mutations = [0 for w in range(len(windows)-1)]
    site_indexes = {}
    for s in ts.sites():
        # get window index and snp index within that window
        window_index = min(np.argwhere(s.position < windows)[0]) - 1
        site_indexes[s.id] = (window_index, num_window_mutations[window_index])
        num_window_mutations[window_index] += 1

    A = [np.zeros((num_mutations, num_mutations))
         for num_mutations in num_window_mutations]

    for v1 in ts.variants():
        w1, m1 = site_indexes[v1.site.id]
        for v2 in ts.variants():
            if v2.position >= v1.position:
                w2, m2 = site_indexes[v2.site.id]
                if w1 == w2:
                    genotypes = list(zip(v1.genotypes, v2.genotypes))
                    sample_genotypes = [[genotypes[sample_index] for
                                        sample_index in ss]
                                        for ss in sample_sets]
                    two_locus_counts = [(gts.count((1, 1)),
                                         gts.count((1, 0)),
                                         gts.count((0, 1)))
                                        for gts in sample_genotypes]
                    A[w1][m1, m2] = function(two_locus_counts, ns)
                    A[w1][m2, m1] = function(two_locus_counts, ns)

    return A


def ld_matrix(ts, sample_sets, function, windows=None):
    """
    Returns the two-locus stats matrix for a given tree sequence and statistic
    function. Optionally, we can specify population ids for multi-pop statistics
    or if we want to compute the statistic for just a subset of samples (say
    if the ts has samples from multiple populations, but we just want r^2 from
    one population). The defaul behavior is to return a single LD matrix for all
    pairwise comparisons across the tree sequence. We can also define windows,
    in which case we return an LD matrix for all pairwise comparisons within
    each window. Windows are non-overlapping and contiguous, but don't
    necessarily need to reach to the tree sequence limits. We could thus specify
    a single window for a subset of the full length if we care about a specific
    region.

    The function's arguments are a list of two-locus haplotype counts
    [(n_AB, n_Ab, n_aB)_0, (n_AB, n_Ab, n_aB)_1, ...] for each population, as
    given in population_ids.

    sample_sets is a list of lists of sample indexes to compute statistics over.
    The length of sample_sets has to match the number of two-locus counts that
    the function takes. If the function is for two populations, sample_sets must
    have length two, for example.. To compute over all samples, can pass
    [ts.samples()]
    """
    ns = [len(ss) for ss in sample_sets]

    if windows is None:
        # we take all mutations (careful, could be quite large)
        windows = [0, ts.sequence_length]
        window_arr = np.array(windows)
        ms = [ts.get_num_mutations()]
    else:
        # get the number of mutations within each window
        # there has to be a better way of doing this...
        ms = [0 for w in range(len(windows)-1)]
        window_arr = np.array(windows)
        for mut in ts.mutations():
            if mut.position < window_arr[0]:
                continue
            if mut.position >= window_arr[-1]:
                break
            w_ind = np.argwhere(np.logical_and(mut.position >= window_arr[:-1],
                                               mut.position < window_arr[1:]))
            ms[w_ind[0][0]] += 1

    # list of empty ld matrices over the specified windows (or entire length, if
    # windows are unspecified)
    A = [np.zeros((m, m), dtype=float) for m in ms]

    # need to keep track of how many mutations within each window we visited
    sites_visited = [0 for w in range(len(ms))]

    # which leaves are in each population, use sets for set intersections
    pop_leaves = [set(ss) for ss in sample_sets]

    for t1 in ts.trees():
        # if tree is outside of window ranges, skip
        if t1.interval[1] < windows[0]:
            continue
        if t1.interval[0] >= windows[-1]:
            break
        # loop over each mutation on this tree, compute statistic with all
        # mutations to the right, within the window
        for sA in t1.sites():
            assert len(sA.mutations) == 1
            # get window for this site
            if sA.position < windows[0] or sA.position >= windows[-1]:
                continue
            w_ind = np.argwhere(np.logical_and(sA.position >= window_arr[:-1],
                                               sA.position < window_arr[1:]))[0][0]

            mA = sA.mutations[0]

            # get the number of leaves carrying this mutation in each pop
            leaves_below_A = set(t1.samples(mA.node))
            nAs = [len(leaves_below_A & pl) for pl in pop_leaves]

            # track the number visited to the right of sA
            sites_visited_to_right = 0

            for t2 in ts.trees(tracked_samples=leaves_below_A):
                if t2.interval[1] < t1.interval[1]:
                    continue
                for sB in t2.sites():
                    assert len(sB.mutations) == 1
                    # check that sB is in the same window
                    if sB.position < windows[w_ind] or sB.position >= windows[w_ind+1]:
                        continue
                    if sB.position < sA.position:
                        continue

                    mB = sB.mutations[0]

                    # get number of leaves carrying B mutation in each pop
                    leaves_below_B = set(t2.samples(mB.node))
                    nBs = [len(leaves_below_B & pl) for pl in pop_leaves]

                    # get the number of samples with AB in each pop
                    nABs = [len(leaves_below_A & leaves_below_B & pl)
                            for pl in pop_leaves]

                    counts = [(nAB, nA-nAB, nB-nAB)
                              for nAB, nA, nB in zip(nABs, nAs, nBs)]

                    # pass counts and sample sizes to two-locus function, then
                    # fill in the matrix for the site in this window
                    val = function(counts, ns)
                    A[w_ind][sites_visited[w_ind],
                             sites_visited[w_ind]+sites_visited_to_right] = val
                    A[w_ind][sites_visited[w_ind]+sites_visited_to_right,
                             sites_visited[w_ind]] = val

                    sites_visited_to_right += 1

            sites_visited[w_ind] += 1

    return A


##############################
# LD score calculator
##############################


def naive_ld_scores(ts, sample_sets, function, window_size=1e5):
    ns = [len(ss) for ss in sample_sets]
    scores = np.zeros(ts.num_mutations)
    for focal_var in ts.variants():
        for linked_var in ts.variants():
            if abs(focal_var.position-linked_var.position) <= window_size:
                genotypes = list(zip(focal_var.genotypes, linked_var.genotypes))
                sample_genotypes = [[genotypes[sample_index] for
                                    sample_index in ss]
                                    for ss in sample_sets]
                two_locus_counts = [(gts.count((1, 1)),
                                     gts.count((1, 0)),
                                     gts.count((0, 1)))
                                    for gts in sample_genotypes]
                scores[focal_var.index] += function(two_locus_counts, ns)
    return scores


def ld_scores(ts, sample_sets, function, window_size=1e5):
    """
    Computes LD scores mutations within the tree sequence. The LD score of a
    SNP is computed as the sum of LD between that focal SNP and all other SNPs
    within a given distance (window_size to left and right).

    A lot of the logic is copied over from the ld_matrix function.
    """
    ns = [len(ss) for ss in sample_sets]

    scores = np.zeros(ts.num_mutations)

    # track current site for indexing the scores
    current_site = 0

    # which leaves are in each population, use sets for set intersections
    pop_leaves = [set(ss) for ss in sample_sets]

    for t1 in ts.trees():
        for sA in t1.sites():
            mA = sA.mutations[0]

            # get the number of leaves carrying this mutation in each pop
            leaves_below_A = set(t1.samples(mA.node))
            nAs = [len(leaves_below_A & pl) for pl in pop_leaves]

            # track the number visited to the right of sA
            sites_visited_to_right = 0

            for t2 in ts.trees(tracked_samples=leaves_below_A):
                if t2.interval[1] < t1.interval[1]:
                    continue
                if t2.interval[0] > t1.interval[1] + window_size:
                    break
                for sB in t2.sites():
                    assert len(sB.mutations) == 1
                    if sB.position < sA.position:
                        continue
                    if sB.position > sA.position + window_size:
                        break

                    mB = sB.mutations[0]

                    # get number of leaves carrying B mutation in each pop
                    leaves_below_B = set(t2.samples(mB.node))
                    nBs = [len(leaves_below_B & pl) for pl in pop_leaves]

                    # get the number of samples with AB in each pop
                    nABs = [len(leaves_below_A & leaves_below_B & pl)
                            for pl in pop_leaves]

                    counts = [(nAB, nA-nAB, nB-nAB) 
                              for nAB, nA, nB in zip(nABs, nAs, nBs)]

                    # pass counts and sample sizes to two-locus function
                    val = function(counts, ns)
                    scores[current_site] += val
                    if sites_visited_to_right > 0:
                        scores[current_site+sites_visited_to_right] += val

                    sites_visited_to_right += 1

            current_site += 1

    return scores


##############################
# Common/example summary functions
##############################


####
# One-populations two-locus functions
####


def r2_function(sample_counts, ns):
    """
    To compute r^2, we take the two-locus haplotype counts and sample size to
    compute haplotype frequencies f. Compute r^2 from f's.
    Returns 0 if either A or B mutation is fixed.
    """
    assert len(sample_counts) == 1, "compute r^2 for only a single population"
    assert len(ns) == 1, "compute r^2 for only a single population"
    (nAB, nAb, naB) = sample_counts[0]
    n = ns[0]
    fAB = nAB / n
    fA = (nAB + nAb) / n
    fB = (nAB + naB) / n
    D = fAB - fA * fB
    if fA != 0 and fA != 1 and fB != 0 and fB != 1:
        r2 = D * D / (fA * (1 - fA) * fB * (1 - fB))
    else:
        r2 = 0
    return r2


def r_function(sample_counts, ns):
    assert len(sample_counts) == 1, "compute r^2 for only a single population"
    assert len(ns) == 1, "compute r^2 for only a single population"
    (nAB, nAb, naB) = sample_counts[0]
    n = ns[0]
    fAB = nAB / n
    fA = (nAB + nAb) / n
    fB = (nAB + naB) / n
    D = fAB - fA * fB
    if fA != 0 and fA != 1 and fB != 0 and fB != 1:
        r = D / np.sqrt(fA * (1 - fA) * fB * (1 - fB))
    else:
        r=0
    return r


####
# Two-populations two-locus functions
####


def r1_r2_function(sample_counts, ns):
    """
    The two population analog to r^2: computes r1 times r2, taking haplotype and
    sample size counts in two populations.
    If the mutation is fixed in either population, returns 0
    """
    assert len(sample_counts) == 2, "compute r1.r2 for only a single population"
    assert len(ns) == 2, "compute r1.r2 for only a single population"
    fAB1 = sample_counts[0][0] / ns[0]
    fAB2 = sample_counts[1][0] / ns[1]
    fA1 = sample_counts[0][1] / ns[0]
    fA2 = sample_counts[1][1] / ns[1]
    fB1 = sample_counts[0][2] / ns[0]
    fB2 = sample_counts[1][2] / ns[1]
    D1 = fAB1 - fA1 * fB1
    D2 = fAB2 - fA2 * fB2
    if fA1 != 0 and fA1 != 1 and fB1 != 0 and fB1 != 1:
        r1 = D1 / np.sqrt(fA1 * (1 - fA1) * fB1 * (1 - fB1))
    else:
        r1 = 0
    if fA2 != 0 and fA2 != 1 and fB2 != 0 and fB2 != 1:
        r2 = D2 / np.sqrt(fA2 * (1 - fA2) * fB2 * (1 - fB2))
    else:
        r2 = 0
    return r1*r2


##############################
# Test implementations
##############################


#class TestLdMatrixCalculator(unittest.TestCase):
#    """
#    Tests for the general LD matrix calculator.
#    """
#    
#    num_test_sites = 50