#Clustering algorithms for signed networks
#Based on Chiang et. al, 2014

from scipy.sparse import csr_matrix, diags
from scipy.linalg import eig, cholesky, LinAlgError
from scipy.sparse.linalg import eigs
import numpy as np
import logging
from itertools import permutations

import svp_sign_prediction as svp
import matrix_factorization as mf
from sklearn.cluster import KMeans
import simulate_networks as sim

from scipy.sparse.linalg.eigen.arpack.arpack import ArpackError

#Perform matrix clustering
#Try to find clusterings such that within-cluster edges are 1 and between-cluster edges are -1
#Input: adjacency matrix
#       number of clusters
#       method to use ("signed laplacian" from previous work, or "matrix completion" from this paper)
#       completion algorithm (if doing matrix completion)
#       params for completion algorithm (if doing matrix completion)
#Output: vector of cluster indicators for each row/column (person) in adjacency matrix
def cluster_signed_network(adj_matrix, cluster_sizes, method, completion_alg=None, params=None, mode="normal"):
  cluster_matrix = None #matrix whose eigenvectors to perform clustering on top of
  num_clusters = len(cluster_sizes)
  method = method.lower()

  #print adj_matrix.A
  #print
  if method == "signed laplacian":
    cluster_matrix = signed_laplacian(adj_matrix)#sl_diag_matrix - adj_matrix #signed Laplacian
    if mode == "test": #test to make sure signed Laplacian is positive semidefinite
      try:
        chol = cholesky(cluster_matrix.A)
      except LinAlgError:
        raise ValueError("Cholesky failed, so signed Laplacian is not PSD...")
    #print cluster_matrix.A
    #print
  elif method == "matrix completion":
    completion_alg == completion_alg.lower()

    #Complete the matrix with desired algorithm
    if completion_alg == "svp":
      try:
        rank, tol, max_iter, step_size = params
        cluster_matrix = svp.sign_prediction_SVP(adj_matrix, rank, tol, max_iter, step_size)
      except:
        logging.exception("Exception: ")
        raise ValueError("check input for SVP")
    elif completion_alg == "sgd":
      try:
        learning_rate, loss_type, tol, max_iters, regularization_param, dim = params
        factor1, factor2 = mf.matrix_factor_SGD(adj_matrix, learning_rate, loss_type, tol, max_iters, regularization_param, dim)
        cluster_matrix = csr_matrix.sign(csr_matrix(factor1*factor2.transpose()))
      except:
        raise ValueError("check input for SGD")
    elif completion_alg == "als":
      try: 
        dim, num_iters = params
        factor1, factor2 = mf.matrix_factor_ALS(adj_matrix, dim, num_iters)
        cluster_matrix = csr_matrix.sign(csr_matrix(factor1.transpose()*factor2))
      except:
        logging.exception()
        raise ValueError("check input for ALS")
    else:
      raise ValueError("unrecognized matrix completion algorithm: ", completion_alg)

    #see how much of underlying complete matrix our completion method recovered
    rows, cols = cluster_matrix.nonzero()
    edges = zip(rows, cols)
    num_correct_edges = 0
    for edge in edges:
      if cluster_matrix[edge] == sim.get_complete_balanced_network_edge_sign(cluster_sizes,edge):
        num_correct_edges += 1
    print "percentage of network recovered: ", float(num_correct_edges) / sum(cluster_sizes)**2
  else:
    raise ValueError("unrecognized clustering method: ", method)

  #Get top k (number of clusters) eigenvectors
  cluster_matrix = cluster_matrix.asfptype()
  try:
    eigenvals, top_eigenvecs = eigs(cluster_matrix, k=num_clusters)
  except ArpackError: #happens when adj matrix is complete sparse matrix with 5 clusters of size 5(???)
    eigenvals, eigenvecs = eig(cluster_matrix.A)
    top_eigenvecs = eigenvecs[:,:num_clusters]
  if mode == "test":
    assert top_eigenvecs.shape == (adj_matrix.shape[0], num_clusters)
  #print top_eigenvecs

  #Perform k-means clustering on top k eigenvectors
  clusters = KMeans(n_clusters = num_clusters)
  cluster_indicators = clusters.fit_predict(top_eigenvecs)

  #test: predicted one cluster for each person
  if mode == "test":
    assert cluster_indicators.size == adj_matrix.shape[0]
  return cluster_indicators

#Compute signed laplacian of a matrix
#Input: matrix (sparse CSR matrix)
#Output: signed laplacian (sparse CSR matrix)
def signed_laplacian(adj_matrix):
  abs_deg_data = list()

  #probably a crude way of constructing signed laplacian
  #(iterate over all entries)
  #but gets the job done for scale of these experiments
  for row_index in range(adj_matrix.shape[0]):
    row = adj_matrix.getrow(row_index).A[0]
    #sum absolute values of all row elements
    #NOTE: paper sums over non-diagonal row elements
    #but earlier sources define absolute degree matrix this way 
    #e.g. http://www.cs.utexas.edu/users/inderjit/public_papers/sign_clustering_cikm12.pdf
    #this definition is also more consistent with typical definition of graph Laplacian
    #furthermore, it shouldn't matter since diagonal edges are basically self-relationships
    #empirically, this change doesn't hurt, maybe helps a little
    abs_deg_data.append(np.count_nonzero(row))
  abs_deg_matrix = diags([abs_deg_data],[0],format="csr") #make diagonal matrix with this data
  return abs_deg_matrix - adj_matrix

#Clustering pipeline
#Simulate data, store ground truth clusters
#Perform clustering algorithm, get predicted clusters
#Compute clustering accuracy
#Input: tuple of network parameters (see simulate_networks.py for details)
#       tuple of clustering parameters (see cluster_signed_network() for details)
#Output: predictions of cluster label (0-num_clusters - 1) for each label
def clustering_pipeline(network_params, clustering_params):
  #Create network
  cluster_sizes, sparsity, noise_prob = network_params
  num_clusters = len(cluster_sizes)

  #NOTE: currently only support up to 6 clusters
  if num_clusters > 6: 
    #holdup is our evaluation of the clusters
    #where we try all permutations of labels to see which ones yield best match
    #to make up for the fact that cluster numbers (labels) are interchangeable
    #without this part (or making this part more efficient), we could do more clusters
    raise ValueError("currently don't support cluster evaluation for more than 6 clusters")
  
  network = sim.sample_network(cluster_sizes, sparsity, noise_prob)
  
  #Assign ground truth labels (the first "cluster_size" are in cluster 0, next are in cluster 1, etc.)
  cluster_labels = list()
  #for cluster_num in range(num_clusters):
  for cluster_index in range(len(cluster_sizes)):
    cluster_labels += [cluster_index] * cluster_sizes[cluster_index]
  cluster_labels = np.asarray(cluster_labels)

  #perform clustering
  cluster_sizes, method, completion_alg, completion_params, mode = clustering_params  
  cluster_predictions = cluster_signed_network(network, cluster_sizes, method, completion_alg, completion_params, mode)
  cluster_accuracy = evaluate_cluster_accuracy(cluster_predictions, cluster_labels, num_clusters)
  return cluster_accuracy

#Evaluate clustering accuracy
#Input: predicted cluster labels
#       ground truth cluster assignments
#       number of clusters
#Output: proportion of correctly clustered
#WARNING THIS IS A BRUTE FORCE METHOD
#WORKS ONLY FOR 6 or so CLUSTERS OR FEWER (FACTORIAL IN NUMBER OF CLUSTERS)
#ALL OUR EXPERIMENTS REQUIRE AND TIME IS SHORT SO HERE GOES
#TODO DO THIS MORE INTELLIGENTLY
def evaluate_cluster_accuracy(cluster_predictions, cluster_labels, num_clusters):
  #kmeans may label clusters differently from ground truth (it just gives them some number)
  #so we may have the right clustering but still be wrong only because kmeans gave cluster elts different labels
  #Brute force: Try all permutations of different cluster names and see which one yields highest accuracy
  max_acc = 0
  for perm in permutations(range(num_clusters)):
    possible_clustering = [perm[label] for label in cluster_predictions]
    max_acc = max(max_acc, np.mean(np.asarray(possible_clustering) == cluster_labels))
  return max_acc

if __name__ == "__main__":
  #Parameters for simulating network
  cluster_sizes = [3,4,5]
  sparsity = 0.5
  noise_prob = 0
  network_params = (cluster_sizes, sparsity, noise_prob)
  mode="test"
  #Parameters for performing clustering
  #'''
  method = "signed laplacian"
  completion_alg = None
  completion_params = None
  
  '''
  method = "matrix completion"
  completion_alg = "svp"
  rank = 10
  tol = 1
  max_iter = 10
  step_size = 1
  completion_params = (rank, tol, max_iter, step_size)
  #'''
  clustering_params = (cluster_sizes, method, completion_alg, completion_params, mode)
  cluster_accuracy = clustering_pipeline(network_params, clustering_params)
  print "Accuracy: ", cluster_accuracy




  