# Filename: siteannotation.py
# Author: Thomas H. Smith 2017

import pandas as pd
from tqdm import tqdm_notebook
import numpy as np
import pandas as pd
import urllib2
from urllib2 import HTTPError, URLError
from multiprocessing.dummy import Pool as ThreadPool
from requests.exceptions import ReadTimeout
import referencedbprocessing
import pkg_resources
import os

# import reference sequences DataFrame
pickle = pkg_resources.resource_filename(__name__, 'data/Phosphosite_seq.pickle')
if os.path.isfile(pickle):
    DF_SEQS = pd.read_pickle(pickle)
    print 'Loaded reference sequences pickle.'
else:
    in_file = pkg_resources.resource_filename(__name__, 'data/Phosphosite_seq.fasta')
    if os.path.isfile(in_file):
        DF_SEQS = referencedbprocessing.import_fasta_seqs(in_file)
        print 'Loaded reference sequences from PhosphoSite file. Saved pickle',\
              'for future use.'
    else:
        print 'Missing pickle or PhosphoSite file for reference sequences. Download',\
              '"Phosphosite_seq.fasta" from http://www.phosphosite.org/staticDownloads.action'

# import known regulatory sites DataFrame
pickle = pkg_resources.resource_filename(__name__, 'data/Regulatory_sites.pickle')
if os.path.isfile(pickle):
    DF_REG = pd.read_pickle(pickle)
    print 'Loaded known functional/regulatory sites data from pickle.'
else:
    in_file = pkg_resources.resource_filename(__name__, 'data/Regulatory_sites')
    if os.path.isfile(in_file):
        DF_REG = referencedbprocessing.import_reported_sites(in_file)
        print 'Loaded known functional/regulatory sites data from PhosphoSite file. Saved',\
              'pickle for future use.'
    else:
        print 'Missing pickle or PhosphoSite file for known functional/regulatory sites. Download',\
              '"Regulatory_sites" file from http://www.phosphosite.org/staticDownloads.action'

# import known sites DataFrame
pickle = pkg_resources.resource_filename(__name__, 'data/Phosphorylation_site_dataset.pickle')
if os.path.isfile(pickle):
    DF_REPORTED = pd.read_pickle(pickle)
    print 'Loaded known phosphosites data from pickle.'
else:
    in_file = pkg_resources.resource_filename(__name__, 'data/Phosphorylation_site_dataset')
    if os.path.isfile(in_file):
        DF_REPORTED = referencedbprocessing.import_reported_sites(in_file)
        print 'Loaded known phosphosites data from PhosphoSite file.  Saved pickle for future use.'
    else:
        print 'Missing pickle or PhosphoSite file for known phosphosites. Download',\
              '"Phosphorylation_site_dataset" from http://www.phosphosite.org/staticDownloads.action'


def _webfetch_uniprot_seq(protein):
    # Helper function
    # Retrieve reference sequence for a protein from uniprot
    # given the uniprot accession ID, return empty str if not found

    # Construct URL string pointing to uniprot fasta file
    url = 'http://www.uniprot.org/uniprot/%s.fasta' % protein
    try: # Catch errors arising from invalid URL
        data = urllib2.urlopen(url)
        seq = ''
        for line in data.readlines()[1:]:
            seq = seq + line.rstrip()
        return seq
    except HTTPError, URLError:
        print '%s: caught HTTPError' % protein
        return ''

# called by each thread
def _thread_helper_func(protein):
    seq = _webfetch_uniprot_seq(protein)
    return {'Uniprot_ID': protein, 'Sequence':seq}

def _build_missing_seqs_df(missing):
    print 'Retrieving missing sequences for %d proteins...' % len(missing)
    pool = ThreadPool(32)
    results = pool.map(_thread_helper_func, missing)
    df_missing_seqs = pd.DataFrame(results)
    print 'Finished retrieving missing sequences.'
    return df_missing_seqs


def _get_full_protein_sequence(uniprot_acc):
    global SEQS_DICT
    if DF_SEQS.Uniprot_ID.str.contains(uniprot_acc).any():
        uniprot_seq = DF_SEQS[DF_SEQS.Uniprot_ID == uniprot_acc].Sequence.values[0]
        return uniprot_seq
    else:
        if SEQS_DICT.has_key(uniprot_acc):
            return SEQS_DICT[uniprot_acc]


def _get_class1_sites(df_in, row_ix, rs_col_name, threshold,
                      include_uncertain=True):
    # Helper function
    # Given df and RS score column name, extract p-sites
    # above given threshold and return as a list of sites
    # in format [S1, T4, y5]
    sites = []
    mysplit = df_in.iloc[row_ix][rs_col_name].split(';')
    for item in mysplit:
        words = item.split('(Phospho):')
        residue = words[0].strip()
        prob = float(words[1])
        if prob >= threshold:
            sites.append(residue)
        else:
            if include_uncertain:
                unsure_res = residue[0].lower() + ''.join(residue[1:])
                sites.append(unsure_res)
    return sites

# Helper function - search full-length protein seq for subseq and index phosph-sites in this context
def _identify_site_in_seq(protein, subseq, site):
    uniprot_seq =  DF_SEQS[DF_SEQS.Uniprot_ID == protein].Sequence.values[0]
    if len(uniprot_seq) < 5:
        return 0
    seq_pos_index = uniprot_seq.find(str.upper(subseq))
    site_pos = int(site[1:]) + seq_pos_index
    full_annot = '%s%d' % (site[0], site_pos)
    return full_annot

def _populate_site_annotation_cols(_df, site):
    df_in = _df.copy()
    j = len(df_in) -1
    protein = df_in.loc[j]['Protein']
    subseq = df_in.loc[j]['Sequence']
    df_in.loc[j, 'LocalSite'] = site
    annot_site = _identify_site_in_seq(protein, subseq, site)
    if annot_site == 0:
        df_in.loc[j, 'AnnotatedSite'] = 'na'
        df_in.loc[j, 'Residue'] = 'na'
        df_in.loc[j, 'Position'] = 0
        return df_in
    else:
        residue = annot_site[0]
        position = annot_site[1:]
        df_in.loc[j, 'AnnotatedSite'] = annot_site
        df_in.loc[j, 'Residue'] = residue
        df_in.loc[j, 'Position'] = position
        return df_in

def process_phosphopeptides(_df, phos_rs_col1, val_cols1, threshold, missing_values=np.nan,
                            phos_rs_col2=0, val_cols2=0):
    if (phos_rs_col2 != 0) & (val_cols2 != 0):
        return _identify_phosphosites_two_runs(_df, phos_rs_col1, val_cols1, threshold, missing_values,
                                        phos_rs_col2, val_cols2)
    else:
        return _identify_phosphosites_one_run(_df, phos_rs_col1, val_cols1, threshold, missing_values)



def _identify_phosphosites_one_run(_df, phos_rs_col1, val_cols1, threshold, missing_values=np.nan):
    """

    Annotate phosphosites over a specified threshold phosRS value
    in the context of corresponding full-length protein sequences.
    Use data acquired from one run. Rows with multiple sites are split
    into multiple rows, one for each phosphosite. Sites under threshold
    are dropped.

    Parameters
    ----------
    _df : pandas.DataFrame
        DataFrame containing data
    phos_rs_col1 : str
        Name of column containing phosRS scores from run 1
    val_cols1 : list of str
        Names of columns containing data values from run 1
    threshold : int
        Minimum phosRS score to include
    missing_values : int, str, or NaN
        Value to impute for missing data

    Returns
    -------
    df_new : pandas.DataFrame
        New DataFrame containing additional rows expanded from peptides
        containing multiple phosphosites, and additional columns containing
        phosphosite annotation data.

    """

    global df_missing_seqs, DF_SEQS
    num_rows_dropped = 0
    df_in = _df.copy()
    df_new = pd.DataFrame()

    # Build df of missing sequences using thread pool
    # this significantly decreases processing time
    known = set(DF_SEQS.Uniprot_ID.unique())
    targets = set(df_in.Protein.unique())
    missing = targets - known
    missing = list(missing)
    if len(missing) > 0:
        df_missing_seqs = _build_missing_seqs_df(missing)
        DF_SEQS = DF_SEQS.append(df_missing_seqs)

    # Annotation
    pbar1 = tqdm_notebook(range(len(df_in)), total=len(df_in))
    for i in pbar1:
        protein = df_in.iloc[i]['Protein']
        subseq = df_in.iloc[i]['Sequence']
        sites1 = _get_class1_sites(df_in, i, phos_rs_col1, threshold, include_uncertain=False)
        if len(sites1) > 0: # If any sites met threshold for run1
            for site in sites1:
                df_new = df_new.append(df_in.iloc[i], ignore_index=True)
                df_new = _populate_site_annotation_cols(df_new, site)
        else: # if sites1 does not contain any sites
            num_rows_dropped += 1
    print 'Dropped '+str(num_rows_dropped)+' rows that failed to meet phosRS'\
    'threshold for at least one run'
    return df_new


def _identify_phosphosites_two_runs(_df, phos_rs_col1, val_cols1, phos_rs_col2,
                                   val_cols2, threshold, missing_values=0):
    """

    Annotate phosphosites over a specified threshold phosRS value
    in the context of corresponding full-length protein sequences.
    Use data acquired from two runs, where each run has a seperate
    phosRS score. Rows with multiple sites are split into multiple
    rows, one for each phosphosite. Sites under threshold are dropped.
    For sites under threshold for only one run, data columns are only
    conserved for the run meeting the threshold, and imputed with
    zeroes or NaN for the other run.

    Parameters
    ----------
    _df : pandas.DataFrame
        DataFrame containing data
    phos_rs_col1 : str
        Name of column containing phosRS scores from run 1
    val_cols1 : list of str
        Names of columns containing data values from run 1
    phos_rs_col2 : str
        Name of column containing phosRS scores from run 2
    val_cols2 : list of str
        Names of columns containing data values from run 2
    threshold : int
        Minimum phosRS score to include
    missing_values : int, str, or NaN
        Value to impute for missing data

    Returns
    -------
    df_new : pandas.DataFrame
        New DataFrame containing additional rows expanded from peptides
        containing multiple phosphosites, and additional columns containing
        phosphosite annotation data.

    """

    global df_missing_seqs, DF_SEQS
    num_rows_dropped = 0
    df_in = _df.copy()
    df_new = pd.DataFrame()

    # Build df of missing sequences using thread pool
    # this significantly decreases processing time
    known = set(DF_SEQS.Uniprot_ID.unique())
    targets = set(df_in.Protein.unique())
    missing = targets - known
    missing = list(missing)
    if len(missing) > 0:
        df_missing_seqs = _build_missing_seqs_df(missing)
        DF_SEQS = DF_SEQS.append(df_missing_seqs)

    # Annotation
    pbar1 = tqdm_notebook(range(len(df_in)), total=len(df_in))
    for i in pbar1:
        protein = df_in.iloc[i]['Protein']
        subseq = df_in.iloc[i]['Sequence']
        if df_in.iloc[i][phos_rs_col1] != 'na':
            sites1 = _get_class1_sites(df_in, i, phos_rs_col1, threshold, include_uncertain=False)
        else: sites1 = []
        if df_in.iloc[i][phos_rs_col2] != 'na':
            sites2 = _get_class1_sites(df_in, i, phos_rs_col2, threshold, include_uncertain=False)
        else: sites2 = []

        if len(sites1) > 0: # If any sites met threshold for run1
            if len(sites2) > 0: # If both runs have at least one site that met threshold
                for site in sites1:
                    if site in sites2: # For a site in both runs, just copy over the data
                        df_new = df_new.append(df_in.iloc[i], ignore_index=True)
                        sites2.remove(site) # Remove site from sites2 to avoid counting twice
                        df_new = _populate_site_annotation_cols(df_new, site)
                    else: # for a site only in run1, copy over data and set val_cols2 = NaN
                        df_new = df_new.append(df_in.iloc[i], ignore_index=True)
                        j = len(df_new) - 1
                        df_new.loc[j, val_cols2] = missing_values
                        df_new = _populate_site_annotation_cols(df_new, site)
                for site in sites2: # finish up by dealing with whatever is left in sites2 list
                    df_new = df_new.append(df_in.iloc[i], ignore_index=True)
                    j = len(df_new) - 1
                    df_new.loc[j, val_cols1] = missing_values
                    df_new = _populate_site_annotation_cols(df_new, site)
            else: # if sites1 has at least one site,
                  # but sites2 is empty, copy over data, set vals2=NaN
                for site in sites1:
                    df_new = df_new.append(df_in.iloc[i], ignore_index=True)
                    j = len(df_new) - 1
                    df_new.loc[j, val_cols2] = missing_values
                    df_new = _populate_site_annotation_cols(df_new, site)
        else: # if sites1 does not contain any sites
            if len(sites2) > 0: # if sites2 contains at least one site
                for site in sites2:
                    df_new = df_new.append(df_in.iloc[i], ignore_index=True)
                    j = len(df_new) - 1
                    df_new.loc[j, val_cols1] = missing_values
                    df_new = _populate_site_annotation_cols(df_new, site)
            else: num_rows_dropped += 1
    print 'Dropped '+str(num_rows_dropped)+' rows that failed to meet phosRS'\
    'threshold for at least one run'
    return df_new


def annotate_functional_sites(_df):
    """

    Annotate functional phosphosites using
    PhosphoSitePlus database as reference

    Parameters
    ----------
    _df : pandas.DataFrame
        DataFrame containing data

    Returns
    -------
    df_new : pandas.DataFrame
        New DataFrame with additional columns
        containing functional site data.

    """

    df_new = _df.copy()
    pbar1 = tqdm_notebook(range(len(df_new)), total=len(df_new))
    for i in pbar1:
        protein = df_new.iloc[i]['Protein']
        gene = df_new.iloc[i]['Gene']
        site = df_new.iloc[i]['AnnotatedSite'] + '-p'
        df_match = DF_REG[(DF_REG['ACC_ID'] == protein) & (DF_REG['MOD_RSD'] == site)]
        if len(df_match > 0):
            df_new.loc[i, 'Functional_site'] = '+'
            df_new.loc[i, 'DOMAIN'] = str(df_match['DOMAIN'].values[0])
            df_new.loc[i, 'ON_FUNCTION'] = str(df_match['ON_FUNCTION'].values[0])
            df_new.loc[i, 'ON_PROCESS'] = str(df_match['ON_PROCESS'].values[0])
            df_new.loc[i, 'ON_PROT_INTERACT'] = str(df_match['ON_PROT_INTERACT'].values[0])
            df_new.loc[i, 'ON_OTHER_INTERACT'] = str(df_match['ON_OTHER_INTERACT'].values[0])
            df_new.loc[i, 'NOTES'] = str(df_match['NOTES'].values[0])
        else:
            df_new.loc[i, 'Functional_site'] = '-'
            df_new.loc[i, 'DOMAIN'] = 0
            df_new.loc[i, 'ON_FUNCTION'] = 0
            df_new.loc[i, 'ON_PROCESS'] = 0
            df_new.loc[i, 'ON_PROT_INTERACT'] = 0
            df_new.loc[i, 'ON_OTHER_INTERACT'] = 0
            df_new.loc[i, 'NOTES'] = 0
        if len(DF_REPORTED[(DF_REPORTED['ACC_ID'] == protein) &
                           (DF_REPORTED['MOD_RSD'] == site)]) > 0:
            df_new.loc[i, 'Known_site'] = '+'
        else:
            if len(DF_REPORTED[(DF_REPORTED['GENE'] == gene) &
                               (DF_REPORTED['MOD_RSD'] == site)]) > 0:
                df_new.loc[i, 'Known_site'] = '+'
            else:
                df_new.loc[i, 'Known_site'] = '-'
    return df_new

