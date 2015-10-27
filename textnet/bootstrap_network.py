from functools import partial

import pyprind

import networkx as nx
import numpy as np
import pandas as pd

from sklearn.metrics import pairwise_distances


def time_grouped_pairwise_distances(X, time_index, metric='cosine', groupby=5, n_jobs=1):
    """
    This is a specialized function to compute the distances between texts and potential
    pre-texts where pre-texts are defined as those texts that are published (or attested)
    prior to or at the same time that the source text was published. The function is 
    basically a little trick that reduced the costly operation of computing all pairwise
    distances in a matrix of stories where we know in advance that certain texts cannot
    be each others nearest neighbor based on their date of appearance. 

    Parameters
    ----------
    X : ndarray or sparse array, shape: (n_samples_X, n_features)
        Input data.
    time_index : ndarray or pandas DatetimeIndex, shape: (n_samples_X), 
        Index corresponding to time points of each sample in X. If supplied,
        neighbors for each item x in X will only consist of samples that occur 
        before or at the time point corresponding with x. Default is None.
    metric : string, or callable
        Valid values for metric are:
            - From scikit-learn: ['cityblock', 'cosine', 'euclidean', 'l1', 'l2',
              'manhattan']. These metrics support sparse matrix inputs.
            - From scipy.spatial.distance: ['braycurtis', 'canberra', 'chebyshev',
              'correlation', 'dice', 'hamming', 'jaccard', 'kulsinski', 'mahalanobis',
              'matching', 'minkowski', 'rogerstanimoto', 'russellrao', 'seuclidean',
              'sokalmichener', 'sokalsneath', 'sqeuclidean', 'yule']
        See the documentation for scipy.spatial.distance for details on these
        metrics. These metrics do not support sparse matrix inputs.
    groupby : integer, default 5
        group the input array X by chunks of n years where n is specified by groupby.
    n_jobs : integer, default 1
        The number of jobs to use for the computation. This works by breaking
        down the pairwise matrix into n_jobs even slices and computing them in
        parallel.          
    """
    if not isinstance(time_index, pd.DatetimeIndex):
        time_index = pd.DatetimeIndex(time_index)
    dm = np.zeros((X.shape[0], X.shape[0]))
    grouped_indices = time_index.year // groupby * groupby
    for year in np.unique(grouped_indices):
        Y_indices = grouped_indices <= year
        X_indices = grouped_indices == year
        if X_indices.sum() > 0 and Y_indices.sum() > 0:
            chunk_dm = pairwise_distances(X[X_indices,: ], Y=X[Y_indices,: ], metric=metric, n_jobs=n_jobs)
            dm[np.ix_(X_indices, Y_indices)] = chunk_dm
    return dm


def all_argmin(array, tol=0.001):
    return np.where(np.abs(array - np.array([np.nanmin(array, axis=1)]).T) < tol)


def bootstrap_neighbors(X, time_index=None, sigma=0.5, sample_prop=0.5, 
                        n_iter=1000, metric="cosine", return_dist=False, 
                        n_jobs=1, all_min=False, grouped_pairwise=False,
                        groupby=5):
    """
    Parameters
    ----------
    X : ndarray or sparse array, shape: (n_samples_X, n_features)
        Input data.
    time_index : ndarray or pandas DatetimeIndex, shape: (n_samples_X), 
        Index corresponding to time points of each sample in X. If supplied,
        neighbors for each item x in X will only consist of samples that occur 
        before or at the time point corresponding with x. Default is None.
    sigma : float, default 0.5
        the threshold percentage of how often a data point must be 
        assigned as nearest neighbor.
    sample_prop : float, default 0.5
        Proportion of random features for each iteration.
    n_iter : integer, default 1000
        Number of bootstrapping iterations.
    metric : string, or callable
        Valid values for metric are:
            - From scikit-learn: ['cityblock', 'cosine', 'euclidean', 'l1', 'l2',
              'manhattan']. These metrics support sparse matrix inputs.
            - From scipy.spatial.distance: ['braycurtis', 'canberra', 'chebyshev',
              'correlation', 'dice', 'hamming', 'jaccard', 'kulsinski', 'mahalanobis',
              'matching', 'minkowski', 'rogerstanimoto', 'russellrao', 'seuclidean',
              'sokalmichener', 'sokalsneath', 'sqeuclidean', 'yule']
        See the documentation for scipy.spatial.distance for details on these
        metrics. These metrics do not support sparse matrix inputs.
    n_jobs : integer, default 1
        The number of jobs to use for the computation. This works by breaking
        down the pairwise matrix into n_jobs even slices and computing them in
        parallel.
    grouped_pairwise : boolean, default False
        Set to True if you want to use the time_grouped_pairwise_distances, 
        False, otherwise. This option is especially useful for large arrays.
    groupby : integer, default 5
        group the input array X by chunks of n years where n is specified by groupby.
        only works in combination with grouped_pairwise
    """
    n_samples, n_features = X.shape
    sample_size = int(n_features * sample_prop)
    neighbors = np.zeros((n_samples, n_samples), dtype=np.float64)
    indices = np.arange(n_samples)
    progress = pyprind.ProgBar(n_iter)
    if grouped_pairwise:
        dist_fn = partial(time_grouped_pairwise_distances, time_index=time_index, 
                          metric=metric, n_jobs=n_jobs, groupby=groupby)
    else:
        dist_fn = partial(pairwise_distances, metric=metric, n_jobs=n_jobs)
    if time_index is not None:
        potential_neighbors = time_index <= time_index[np.newaxis].T        
    for iteration in range(n_iter):
        rnd_features = np.random.randint(n_features, size=sample_size)
        _X = X[:, rnd_features]
        dm = dist_fn(_X)
        np.fill_diagonal(dm, np.inf)
        if time_index is not None:
            dm[~potential_neighbors] = np.nan # no fix yet for problem of first text
        if all_min:
            neighbors[all_argmin(dm)] += 1
        else:
            neighbors[indices, np.nanargmin(dm, axis=1)] += 1
        progress.update()
    neighbors /= n_iter
    return neighbors


def to_graph(choices, time_index=False, sigma=0.5, only_best=False):
    """
    Parameters
    ----------
    choices : ndarray, shape: (n_samples, n_samples)
        Proportion of assignments resulting from bootstrap_neighbors
    time_index : ndarray or pandas DatetimeIndex, shape: (n_samples_X), 
        Index corresponding to time points of each sample in X. If supplied,
        neighbors for each item x in X will only consist of samples that occur 
        before or at the time point corresponding with x. Default is None.
    sigma : float, default 0.5
        the threshold percentage of how often a data point must be 
        assigned as nearest neighbor.
    only_best : boolean, default False
        Make connections solely between top assignments or all assignments 
        above threshold sigma.
    """
    G = nx.DiGraph()
    for i, neighbors in enumerate(choices):
        G.add_node(i, date=time_index[i] if time_index is not False else None)
        if only_best:
            best = np.argmax(neighbors)
            neighbors = np.array([best]) if neighbors[best] >= sigma else np.array([])
        else:
            neighbors = np.where(neighbors >= sigma)[0]
        if neighbors.shape[0] > 0:
            for neighbor in neighbors:
                G.add_node(neighbor, date=time_index[neighbor] if time_index is not False else None)
                G.add_edge(i, neighbor)
    return G


def bootstrap_network(X, labels=None, time_index=None, sigma=0.5, sample_prop=0.5, 
                      n_iter=1000, metric="cosine", n_jobs=1, only_best=False, all_min=False):
    """
    Parameters
    ----------
    X : ndarray or sparse array, shape: (n_samples_X, n_features)
        Input data.
    labels : iterable of strings, shape: n_samples_X
        Labels corresponding to each sample in X
    time_index : ndarray or pandas DatetimeIndex, shape: (n_samples_X), 
        Index corresponding to time points of each sample in X. If supplied,
        neighbors for each item x in X will only consist of samples that occur 
        before or at the time point corresponding with x. Default is None.
    sigma : float, default 0.5
        the threshold percentage of how often a data point must be 
        assigned as nearest neighbor.
    sample_prop : float, default 0.5
        Proportion of random features for each iteration.
    n_iter : integer, default 1000
        Number of bootstrapping iterations.
    metric : string, or callable
        Valid values for metric are:
            - From scikit-learn: ['cityblock', 'cosine', 'euclidean', 'l1', 'l2',
              'manhattan']. These metrics support sparse matrix inputs.
            - From scipy.spatial.distance: ['braycurtis', 'canberra', 'chebyshev',
              'correlation', 'dice', 'hamming', 'jaccard', 'kulsinski', 'mahalanobis',
              'matching', 'minkowski', 'rogerstanimoto', 'russellrao', 'seuclidean',
              'sokalmichener', 'sokalsneath', 'sqeuclidean', 'yule']
            See the documentation for scipy.spatial.distance for details on these
            metrics. These metrics do not support sparse matrix inputs.
    n_jobs : integer, default 1
        The number of jobs to use for the computation. This works by breaking
        down the pairwise matrix into n_jobs even slices and computing them in
        parallel.
    """
    neighbors = bootstrap_neighbors(X, time_index=time_index, sigma=sigma,
                                    sample_prop=sample_prop, n_iter=n_iter, 
                                    metric=metric, n_jobs=n_jobs, all_min=all_min)
    if len(set(labels)) != X.shape[0]:
        raise ValueError("Number of unique labels should be equal to number of data points.")
    labels = np.arange(X.shape[0]) if labels is None else labels
    return to_graph(neighbors, time_index=time_index, sigma=sigma, only_best=only_best)    


def evolving_graphs(choices, time_index, groupby=lambda x: x, sigma=0.5):
    """
    Create a story network at various points in time, based on the groupby function.
    The function expects a time_index with Datetime objects. This allows you to group
    the graph on years, months or any other time frame you want. 

    >>> groupby = lambda x: x.year # create graphs for each year
    >>> groupby = lambda x: x.year // 10 * 10 # create graphs for each decade
    >>> groupby = pd.TimeGrouper(freq='M') # create graphs for each month

    Parameters
    ----------
    choices : ndarray, shape: (n_samples, n_samples)
        Proportion of assignments resulting from bootstrap_neighbors
    time_index : ndarray or pandas DatetimeIndex, shape: (n_samples_X), 
        Index corresponding to time points of each sample in X. If supplied,
        neighbors for each item x in X will only consist of samples that occur 
        before or at the time point corresponding with x. Default is None.
    groupby : callable
        Function specifying the time steps at which the graphs should be created
    sigma : float, default 0.5
        The threshold percentage of how often a data point must be 
        assigned as nearest neighbor.
    """
    choices_df = pd.DataFrame(choices, index=time_index).sort_index()
    G = nx.DiGraph()
    i = 0
    for group_id, stories in choices_df.groupby(groupby):
        for neighbors in stories.values:
            index = i
            neighbors = np.where(neighbors >= sigma)[0]
            G.add_node(index, date=time_index[index] if time_index is not False else None)
            if neighbors.shape[0] > 0:
                for neighbor in neighbors:
                    G.add_node(neighbor, date=time_index[neighbor] if time_index is not False else None)
                    G.add_edge(index, neighbor)
            i += 1
        yield group_id, G
