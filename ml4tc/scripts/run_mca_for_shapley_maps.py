"""Runs MCA (maximum-covariance analysis) for maps of Shapley values."""

import os
import copy
import shutil
import argparse
import numpy
import xarray
from sklearn.decomposition import IncrementalPCA
from gewittergefahr.gg_utils import error_checking
from gewittergefahr.gg_utils import file_system_utils
from ml4tc.machine_learning import saliency
from ml4tc.scripts import \
    get_shap_predictor_covariance_matrix as get_covar_matrix

TOLERANCE = 1e-6
SEPARATOR_STRING = '\n\n' + '*' * 50 + '\n\n'

LAG_TIME_INDEX = -1
CHANNEL_INDEX = 0

PRINCIPAL_COMPONENT_DIM = 'principal_component'
GRID_ROW_DIM = 'grid_row'
GRID_COLUMN_DIM = 'grid_column'
PIXEL_DIM = 'pixel'

SHAPLEY_SINGULAR_VALUE_KEY = 'shapley_singular_value'
PREDICTOR_SINGULAR_VALUE_KEY = 'predictor_singular_value'
SHAPLEY_EXPANSION_COEFF_KEY = 'shapley_expansion_coefficient'
PREDICTOR_EXPANSION_COEFF_KEY = 'predictor_expansion_coefficient'
EIGENVALUE_KEY = 'eigenvalue'
REGRESSED_SHAPLEY_VALUE_KEY = 'regressed_shapley_value'
REGRESSED_PREDICTOR_KEY = 'regressed_predictor'

SHAPLEY_FILES_ARG_NAME = 'input_shapley_file_names'
COVARIANCE_FILE_ARG_NAME = 'input_covariance_file_name'
OUTPUT_FILE_ARG_NAME = 'output_file_name'

SHAPLEY_FILES_HELP_STRING = (
    'List of paths to Shapley files, each containing Shapley values for a '
    'different set of examples (one example = one TC at one time).  These '
    'files will be read by `saliency.read_file`.'
)
COVARIANCE_FILE_HELP_STRING = (
    'Path to covariance file.  This should contain the P-by-P covariance '
    'matrix (where P = num pixels) between the Shapley and predictor values, '
    'created by the script get_shap_predictor_covariance_matrix.py, using the '
    'exact same Shapley files.'
)
OUTPUT_FILE_HELP_STRING = (
    'Path to output file (zarr format).  Parameters of the fitted MCA will be '
    'written here by `_write_mca_results`.'
)

INPUT_ARG_PARSER = argparse.ArgumentParser()
INPUT_ARG_PARSER.add_argument(
    '--' + SHAPLEY_FILES_ARG_NAME, type=str, nargs='+', required=True,
    help=SHAPLEY_FILES_HELP_STRING
)
INPUT_ARG_PARSER.add_argument(
    '--' + COVARIANCE_FILE_ARG_NAME, type=str, required=True,
    help=COVARIANCE_FILE_HELP_STRING
)
INPUT_ARG_PARSER.add_argument(
    '--' + OUTPUT_FILE_ARG_NAME, type=str, required=True,
    help=OUTPUT_FILE_HELP_STRING
)


def _read_covariance_matrix(covariance_file_name):
    """Reads covariance matrix from NetCDF or (ideally) zarr file.

    P = number of pixels

    :param covariance_file_name: Path to input file.
    :return: covariance_matrix: P-by-P numpy array of covariances, where the
        [i, j] element is the covariance between normalized Shapley value at the
        [i]th pixel and normalized predictor value at the [j]th pixel.
    """

    # TODO(thunderhoser): This is HACK to deal with change from NetCDF to zarr.
    if (
            covariance_file_name.endswith('.nc')
            and not os.path.isfile(covariance_file_name)
    ):
        covariance_file_name = '{0:s}.zarr'.format(covariance_file_name[:-3])

    if (
            covariance_file_name.endswith('.zarr')
            and not os.path.isdir(covariance_file_name)
    ):
        covariance_file_name = '{0:s}.nc'.format(covariance_file_name[:-5])

    if covariance_file_name.endswith('.zarr'):
        return xarray.open_zarr(covariance_file_name)[
            get_covar_matrix.COVARIANCE_KEY
        ].values

    if not covariance_file_name.endswith('.nc'):
        return None

    covariance_matrix = xarray.open_dataset(covariance_file_name)[
        get_covar_matrix.COVARIANCE_KEY
    ].values

    netcdf_file_name = copy.deepcopy(covariance_file_name)
    zarr_file_name = '{0:s}.zarr'.format(covariance_file_name[:-3])

    print('Writing covariance matrix to: "{0:s}"...'.format(zarr_file_name))
    get_covar_matrix._write_results(
        zarr_file_name=zarr_file_name, covariance_matrix=covariance_matrix
    )

    os.remove(netcdf_file_name)

    return covariance_matrix


def _write_mca_results(
        zarr_file_name,
        shapley_singular_value_matrix, predictor_singular_value_matrix,
        shapley_expansion_coeff_matrix, predictor_expansion_coeff_matrix,
        eigenvalues, regressed_shapley_matrix, regressed_predictor_matrix):
    """Writes MCA results to zarr file.

    P = number of principal components
    M = number of rows in grid
    N = number of columns in grid

    :param zarr_file_name: Path to output file.
    :param shapley_singular_value_matrix: MN-by-P numpy array, where each column
        is a singular vector for the Shapley values.
    :param predictor_singular_value_matrix: MN-by-P numpy array, where each
        column is a singular vector for the predictor values.
    :param shapley_expansion_coeff_matrix: MN-by-P numpy array, where each
        column is a vector of expansion coefficients for the Shapley values.
    :param predictor_expansion_coeff_matrix: MN-by-P numpy array, where each
        column is a vector of expansion coefficients for the predictor values.
    :param eigenvalues: length-P numpy array of eigenvalues.
    :param regressed_shapley_matrix: P-by-M-by-N numpy array of Shapley values
        regressed onto singular vectors.
    :param regressed_predictor_matrix: P-by-M-by-N numpy array of predictor
        values regressed onto singular vectors.
    """

    error_checking.assert_is_string(zarr_file_name)
    if os.path.isdir(zarr_file_name):
        shutil.rmtree(zarr_file_name)

    file_system_utils.mkdir_recursive_if_necessary(
        directory_name=zarr_file_name
    )

    num_principal_components = shapley_singular_value_matrix.shape[1]
    num_grid_rows = regressed_shapley_matrix.shape[1]
    num_grid_columns = regressed_shapley_matrix.shape[2]
    num_pixels = num_grid_rows * num_grid_columns

    pc_indices = numpy.linspace(
        0, num_principal_components - 1, num=num_principal_components, dtype=int
    )
    row_indices = numpy.linspace(
        0, num_grid_rows - 1, num=num_grid_rows, dtype=int
    )
    column_indices = numpy.linspace(
        0, num_grid_columns - 1, num=num_grid_columns, dtype=int
    )
    pixel_indices = numpy.linspace(0, num_pixels - 1, num=num_pixels, dtype=int)

    metadata_dict = {
        PRINCIPAL_COMPONENT_DIM: pc_indices,
        GRID_ROW_DIM: row_indices,
        GRID_COLUMN_DIM: column_indices,
        PIXEL_DIM: pixel_indices
    }

    main_data_dict = {
        SHAPLEY_SINGULAR_VALUE_KEY: (
            (PIXEL_DIM, PRINCIPAL_COMPONENT_DIM),
            shapley_singular_value_matrix
        ),
        PREDICTOR_SINGULAR_VALUE_KEY: (
            (PIXEL_DIM, PRINCIPAL_COMPONENT_DIM),
            predictor_singular_value_matrix
        ),
        SHAPLEY_EXPANSION_COEFF_KEY: (
            (PRINCIPAL_COMPONENT_DIM, PRINCIPAL_COMPONENT_DIM),
            shapley_expansion_coeff_matrix
        ),
        PREDICTOR_EXPANSION_COEFF_KEY: (
            (PRINCIPAL_COMPONENT_DIM, PRINCIPAL_COMPONENT_DIM),
            predictor_expansion_coeff_matrix
        ),
        EIGENVALUE_KEY: (
            (PRINCIPAL_COMPONENT_DIM,),
            eigenvalues
        ),
        REGRESSED_SHAPLEY_VALUE_KEY: (
            (PRINCIPAL_COMPONENT_DIM, GRID_ROW_DIM, GRID_COLUMN_DIM),
            regressed_shapley_matrix
        ),
        REGRESSED_PREDICTOR_KEY: (
            (PRINCIPAL_COMPONENT_DIM, GRID_ROW_DIM, GRID_COLUMN_DIM),
            regressed_predictor_matrix
        )
    }

    mca_table_xarray = xarray.Dataset(
        data_vars=main_data_dict, coords=metadata_dict
    )

    encoding_dict = {
        SHAPLEY_SINGULAR_VALUE_KEY: {'dtype': 'float32'},
        PREDICTOR_SINGULAR_VALUE_KEY: {'dtype': 'float32'},
        SHAPLEY_EXPANSION_COEFF_KEY: {'dtype': 'float32'},
        PREDICTOR_EXPANSION_COEFF_KEY: {'dtype': 'float32'},
        EIGENVALUE_KEY: {'dtype': 'float32'},
        REGRESSED_SHAPLEY_VALUE_KEY: {'dtype': 'float32'},
        REGRESSED_PREDICTOR_KEY: {'dtype': 'float32'}
    }
    mca_table_xarray.to_zarr(
        store=zarr_file_name, mode='w', encoding=encoding_dict
    )


def _run(shapley_file_names, covariance_file_name, output_file_name):
    """Runs MCA (maximum-covariance analysis) for maps of Shapley values.

    This is effectively the same method.

    :param shapley_file_names: See documentation at top of file.
    :param covariance_file_name: Same.
    :param output_file_name: Same.
    """

    print('Reading data from: "{0:s}"...'.format(covariance_file_name))
    covariance_matrix = _read_covariance_matrix(covariance_file_name)
    num_covariance_pixels = covariance_matrix.shape[0]

    shapley_matrix = None
    norm_predictor_matrix = None
    spatial_coarsening_factor = None

    # TODO(thunderhoser): Ensure matching saliency metadata for input files.
    for this_file_name in shapley_file_names:
        print('Reading data from: "{0:s}"...'.format(this_file_name))
        this_saliency_dict = saliency.read_file(this_file_name)

        this_dummy_input_grad_matrix = this_saliency_dict[
            saliency.THREE_INPUT_GRAD_KEY
        ][0][..., LAG_TIME_INDEX, CHANNEL_INDEX]

        this_dummy_saliency_matrix = this_saliency_dict[
            saliency.THREE_SALIENCY_KEY
        ][0][..., LAG_TIME_INDEX, CHANNEL_INDEX]

        if spatial_coarsening_factor is None:
            num_orig_pixels = (
                this_dummy_saliency_matrix.shape[1] *
                this_dummy_saliency_matrix.shape[2]
            )
            spatial_coarsening_factor_float = numpy.sqrt(
                float(num_orig_pixels) / num_covariance_pixels
            )
            spatial_coarsening_factor = int(numpy.round(
                spatial_coarsening_factor_float
            ))

            assert numpy.isclose(
                spatial_coarsening_factor, spatial_coarsening_factor_float,
                rtol=0.01
            )

        this_dummy_input_grad_matrix = this_dummy_input_grad_matrix[
            :, ::spatial_coarsening_factor, ::spatial_coarsening_factor
        ]
        this_dummy_saliency_matrix = this_dummy_saliency_matrix[
            :, ::spatial_coarsening_factor, ::spatial_coarsening_factor
        ]

        this_predictor_matrix = numpy.divide(
            this_dummy_input_grad_matrix, this_dummy_saliency_matrix
        )
        this_predictor_matrix[
            numpy.invert(numpy.isfinite(this_predictor_matrix))
        ] = 0.

        this_shapley_matrix = this_dummy_input_grad_matrix

        if shapley_matrix is None:
            shapley_matrix = this_shapley_matrix + 0.
            norm_predictor_matrix = this_predictor_matrix + 0.
        else:
            shapley_matrix = numpy.concatenate(
                (shapley_matrix, this_shapley_matrix), axis=0
            )
            norm_predictor_matrix = numpy.concatenate(
                (norm_predictor_matrix, this_predictor_matrix), axis=0
            )

    print(SEPARATOR_STRING)

    mean_shapley_value = numpy.mean(shapley_matrix)
    stdev_shapley_value = numpy.std(shapley_matrix, ddof=1)
    norm_shapley_matrix = (
        (shapley_matrix - mean_shapley_value) / stdev_shapley_value
    )
    del shapley_matrix

    mean_predictor_value = numpy.mean(norm_predictor_matrix)
    stdev_predictor_value = numpy.std(norm_predictor_matrix, ddof=1)
    double_norm_predictor_matrix = (
        (norm_predictor_matrix - mean_predictor_value) / stdev_predictor_value
    )
    del norm_predictor_matrix

    num_examples = norm_shapley_matrix.shape[0]
    num_grid_rows = norm_shapley_matrix.shape[1]
    num_grid_columns = norm_shapley_matrix.shape[2]
    num_pixels = num_grid_rows * num_grid_columns
    these_dim = (num_examples, num_pixels)

    norm_shapley_matrix = numpy.reshape(norm_shapley_matrix, these_dim)
    double_norm_predictor_matrix = numpy.reshape(
        double_norm_predictor_matrix, these_dim
    )

    print('Running PCA...')
    pca_object = IncrementalPCA(n_components=num_examples, whiten=False)
    pca_object.fit(covariance_matrix)

    predictor_singular_value_matrix = numpy.transpose(pca_object.components_)
    eigenvalues = pca_object.singular_values_ ** 2

    print('Computing left singular vectors (for Shapley values)...')
    first_matrix = numpy.dot(
        covariance_matrix, predictor_singular_value_matrix
    )
    second_matrix = numpy.linalg.inv(numpy.diag(numpy.sqrt(eigenvalues)))
    shapley_singular_value_matrix = numpy.dot(first_matrix, second_matrix)

    del covariance_matrix

    print('Computing expansion coefficients...')
    shapley_expansion_coeff_matrix = numpy.dot(
        norm_shapley_matrix, shapley_singular_value_matrix
    )
    predictor_expansion_coeff_matrix = numpy.dot(
        double_norm_predictor_matrix, predictor_singular_value_matrix
    )

    print('Standardizing expansion coefficients...')
    these_means = numpy.mean(
        shapley_expansion_coeff_matrix, axis=0, keepdims=True
    )
    these_stdevs = numpy.std(
        shapley_expansion_coeff_matrix, ddof=1, axis=0, keepdims=True
    )
    shapley_expansion_coeff_matrix = (
        (shapley_expansion_coeff_matrix - these_means) / these_stdevs
    )

    these_means = numpy.mean(
        predictor_expansion_coeff_matrix, axis=0, keepdims=True
    )
    these_stdevs = numpy.std(
        predictor_expansion_coeff_matrix, ddof=1, axis=0, keepdims=True
    )
    predictor_expansion_coeff_matrix = (
        (predictor_expansion_coeff_matrix - these_means) / these_stdevs
    )

    print('Regressing Shapley values onto each left singular vector...')
    regressed_shapley_matrix = numpy.full((num_examples, num_pixels), numpy.nan)

    for i in range(num_examples):
        this_matrix = numpy.dot(
            numpy.transpose(norm_shapley_matrix),
            shapley_expansion_coeff_matrix[:, [i]]
        )
        regressed_shapley_matrix[i, :] = (
            numpy.squeeze(this_matrix) / num_examples
        )

    print('Regressing predictor values onto each right singular vector...')
    regressed_predictor_matrix = numpy.full(
        (num_examples, num_pixels), numpy.nan
    )

    for i in range(num_examples):
        this_matrix = numpy.dot(
            numpy.transpose(double_norm_predictor_matrix),
            predictor_expansion_coeff_matrix[:, [i]]
        )
        regressed_predictor_matrix[i, :] = (
            numpy.squeeze(this_matrix) / num_examples
        )

    regressed_shapley_matrix = numpy.reshape(
        regressed_shapley_matrix,
        (num_examples, num_grid_rows, num_grid_columns)
    )
    regressed_predictor_matrix = numpy.reshape(
        regressed_predictor_matrix,
        (num_examples, num_grid_rows, num_grid_columns)
    )

    print('Writing results to: "{0:s}"...'.format(output_file_name))
    _write_mca_results(
        zarr_file_name=output_file_name,
        shapley_singular_value_matrix=shapley_singular_value_matrix,
        predictor_singular_value_matrix=predictor_singular_value_matrix,
        shapley_expansion_coeff_matrix=shapley_expansion_coeff_matrix,
        predictor_expansion_coeff_matrix=predictor_expansion_coeff_matrix,
        eigenvalues=eigenvalues,
        regressed_shapley_matrix=regressed_shapley_matrix,
        regressed_predictor_matrix=regressed_predictor_matrix
    )


if __name__ == '__main__':
    INPUT_ARG_OBJECT = INPUT_ARG_PARSER.parse_args()

    _run(
        shapley_file_names=getattr(INPUT_ARG_OBJECT, SHAPLEY_FILES_ARG_NAME),
        covariance_file_name=getattr(
            INPUT_ARG_OBJECT, COVARIANCE_FILE_ARG_NAME
        ),
        output_file_name=getattr(INPUT_ARG_OBJECT, OUTPUT_FILE_ARG_NAME)
    )
