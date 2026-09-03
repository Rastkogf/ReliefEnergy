# -*- coding: utf-8 -*-

"""
/***************************************************************************
 ReliefEnergy
                                 A QGIS plugin
 Relief energy processing tool
 ***************************************************************************/
"""

__author__ = 'Rastko Glisic'
__date__ = '2026-07-20'
__copyright__ = '(C) 2026 by Rastko Glisic'
__revision__ = '$Format:%H$'

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (QgsProcessing,
                       QgsProcessingException,
                       QgsProcessingAlgorithm,
                       QgsProcessingParameterRasterLayer,
                       QgsProcessingParameterExtent,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterRasterDestination,
                       QgsProcessingParameterFeatureSink,
                       QgsFeatureSink,
                       QgsVectorLayer,
                       QgsFeature,
                       QgsField,
                       QgsSpatialIndex,
                       QgsProcessingUtils,
                       QgsWkbTypes,
                       QgsProject,
                       QgsProcessingLayerPostProcessorInterface,
                       QgsMapLayer,
                       QgsRasterBandStats,
                       QgsColorRampShader,
                       QgsRasterShader,
                       QgsSingleBandPseudoColorRenderer,
                       QgsStyle)
import processing


class EqualIntervalPostProcessor(QgsProcessingLayerPostProcessorInterface):
    """
    Automatically styles the output raster using an explicit Equal Interval classification,
    and forces the QGIS GUI to respect the newly generated classes.
    """
    def instance(self):
        return self

    def postProcessLayer(self, layer, context, feedback):
        from qgis.core import (QgsMapLayer, QgsRasterBandStats, QgsColorRampShader, 
                               QgsRasterShader, QgsSingleBandPseudoColorRenderer, 
                               QgsStyle, QgsRasterMinMaxOrigin)
        
        if layer.type() != QgsMapLayer.RasterLayer:
            return

        provider = layer.dataProvider()
        
        # 1. Force QGIS to actively calculate the true raster statistics
        stats = provider.bandStatistics(1, QgsRasterBandStats.All, layer.extent(), 0)
        
        # 2. Configure the shader exactly how the QGIS UI expects it
        shader = QgsColorRampShader()
        shader.setClassificationMode(QgsColorRampShader.EqualInterval)
        shader.setColorRampType(QgsColorRampShader.Discrete) # Forces distinct blocks in the UI
        shader.setMinimumValue(stats.minimumValue)
        shader.setMaximumValue(stats.maximumValue)
        
        # Apply the color ramp
        ramp = QgsStyle.defaultStyle().colorRamp('Spectral')
        shader.setSourceColorRamp(ramp)
        
        # Native calculation of the 5 intervals
        shader.classifyColorRamp(5, 1, layer.extent(), provider)
        
        # 3. Bind the configured shader to the layer
        raster_shader = QgsRasterShader()
        raster_shader.setRasterShaderFunction(shader)
        
        renderer = QgsSingleBandPseudoColorRenderer(provider, 1, raster_shader)
        
        # Lock the Min/Max origin so QGIS doesn't auto-guess upon loading
        minmax = QgsRasterMinMaxOrigin()
        minmax.setLimits(QgsRasterMinMaxOrigin.MinMax)
        minmax.setStatAccuracy(QgsRasterMinMaxOrigin.Exact)
        renderer.setMinMaxOrigin(minmax)
        
        layer.setRenderer(renderer)
        
        # 4. Emit signals to force the QGIS GUI to lock these changes in
        layer.setCustomProperty("processing_styled", True)
        layer.styleChanged.emit()
        layer.triggerRepaint()


class ReliefEnergyAnomalyAlgorithm(QgsProcessingAlgorithm):
    """
    Calculates Terrain/Relief Energy Anomaly.
    """
    
    DEM_INPUT = 'DEM_INPUT'
    AOI_EXTENT = 'AOI_EXTENT'
    GRID_SIZE = 'GRID_SIZE'
    OUTPUT_CENTROIDS = 'OUTPUT_CENTROIDS' 
    OUTPUT_RASTER = 'OUTPUT_RASTER'       

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return ReliefEnergyAnomalyAlgorithm()

    def name(self):
        return 'reliefenergyanomaly'

    def displayName(self):
        return self.tr('Calculate Relief Energy Anomaly')

    def group(self):
        return self.tr('Terrain Analysis')

    def groupId(self):
        return 'terrainanalysis'

    def shortHelpString(self):
        return self.tr("Calculates localized relief energy anomalies using a global average and moving window smoothing.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM_INPUT, self.tr('Input DEM')))
        self.addParameter(QgsProcessingParameterExtent(self.AOI_EXTENT, self.tr('Area of Interest (Extent)')))
        self.addParameter(QgsProcessingParameterNumber(
            self.GRID_SIZE, self.tr('Grid Square Dimension (meters)'),
            type=QgsProcessingParameterNumber.Double, defaultValue=4000.0
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_CENTROIDS, self.tr('Output Centroids (Anomaly Points)'), QgsProcessing.TypeVectorPoint
        ))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_RASTER, self.tr('Relief Anomaly Raster')
        ))

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM_INPUT, context)
        aoi_extent = self.parameterAsExtent(parameters, self.AOI_EXTENT, context)
        grid_size = self.parameterAsDouble(parameters, self.GRID_SIZE, context)
        
        if dem_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.DEM_INPUT))
            
        dem_crs = dem_layer.crs()
        project_crs = QgsProject.instance().crs()
        
        # ---------------------------------------------------------------------
        # STEP 1: Generate the Grid
        # ---------------------------------------------------------------------
        feedback.pushInfo("Generating grid...")
        grid_result = processing.run("native:creategrid", {
            'TYPE': 2, 
            'EXTENT': aoi_extent,
            'HSPACING': grid_size,
            'VSPACING': grid_size,
            'HOVERLAY': 0,
            'VOVERLAY': 0,
            'CRS': project_crs,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback, is_child_algorithm=True)
        
        grid_layer = grid_result['OUTPUT']

        # ---------------------------------------------------------------------
        # STEP 1.5: Align Raster CRS (Reproject DEM)
        # ---------------------------------------------------------------------
        feedback.pushInfo("Reprojecting DEM to match Project CRS...")
        warp_result = processing.run("gdal:warpreproject", {
            'INPUT': dem_layer,
            'SOURCE_CRS': dem_crs,
            'TARGET_CRS': project_crs,
            'NODATA': None,
            'TARGET_RESOLUTION': None, 
            'OPTIONS': '',
            'DATA_TYPE': 0,
            'TARGET_EXTENT': aoi_extent,
            'TARGET_EXTENT_CRS': project_crs,
            'MULTITHREADING': True,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }, context=context, feedback=feedback, is_child_algorithm=True)
        
        aligned_dem = warp_result['OUTPUT']

        # ---------------------------------------------------------------------
        # STEP 2 & 3: Zonal Statistics (MIN & MAX elevation)
        # ---------------------------------------------------------------------
        feedback.pushInfo("Calculating Min/Max elevation per grid cell...")
        zonal_result = processing.run("native:zonalstatisticsfb", {
            'INPUT': grid_layer,
            'INPUT_RASTER': aligned_dem, 
            'RASTER_BAND': 1,
            'COLUMN_PREFIX': 'z_',
            'STATISTICS': [5, 6], 
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback, is_child_algorithm=True)
        
        z_layer = QgsProcessingUtils.mapLayerFromString(zonal_result['OUTPUT'], context)

        # ---------------------------------------------------------------------
        # STEP 4: Centroids, deltaH, deltaD, and Anomalies
        # ---------------------------------------------------------------------
        feedback.pushInfo("Calculating deltaH and centroids...")
        
        fields_h = QgsField("deltaH", QVariant.Double)
        fields_a1 = QgsField("anomaly_1", QVariant.Double)
        fields_a2 = QgsField("anomaly_2", QVariant.Double)
        
        out_fields = z_layer.fields()
        out_fields.append(fields_h)
        out_fields.append(fields_a1)
        out_fields.append(fields_a2)

        active_crs = z_layer.crs()

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT_CENTROIDS, context,
            out_fields, QgsWkbTypes.Point, active_crs
        )

        temp_points = QgsVectorLayer(f"Point?crs={active_crs.authid()}", "temp", "memory")
        pr = temp_points.dataProvider()
        pr.addAttributes(out_fields.toList())
        temp_points.updateFields()

        features_to_add = []
        for f in z_layer.getFeatures():
            z_min = f['z_min']
            z_max = f['z_max']
            
            if not isinstance(z_min, (int, float)) or not isinstance(z_max, (int, float)):
                continue
                
            deltaH = z_max - z_min
            
            new_f = QgsFeature(temp_points.fields())
            new_f.setGeometry(f.geometry().centroid())
            
            attrs = f.attributes()
            attrs.extend([deltaH, 0.0, 0.0])
            new_f.setAttributes(attrs)
            
            features_to_add.append(new_f)
            
        pr.addFeatures(features_to_add)
        temp_points.updateExtents()
        
        num_points = temp_points.featureCount()
        
        if num_points == 0:
            feedback.reportError("No valid points generated! Ensure the AOI extent overlaps the DEM and their CRSs match.")
            return {self.OUTPUT_CENTROIDS: dest_id, self.OUTPUT_RASTER: parameters[self.OUTPUT_RASTER]}
            
        feedback.pushInfo("Calculating global deltaD and smoothing anomalies...")
        
        deltaH_lookup = {feat.id(): feat['deltaH'] for feat in temp_points.getFeatures()}
        deltaD = sum(deltaH_lookup.values()) / num_points
        
        spatial_index = QgsSpatialIndex(temp_points.getFeatures())
        
        idx_a1 = temp_points.fields().indexOf("anomaly_1")
        idx_a2 = temp_points.fields().indexOf("anomaly_2")
        
        for f in temp_points.getFeatures():
            anomaly_1 = f['deltaH'] - deltaD
            
            neighbor_ids = spatial_index.nearestNeighbor(f.geometry(), 9)
            
            neighbor_a1_values = [(deltaH_lookup[n_id] - deltaD) for n_id in neighbor_ids]
            anomaly_2 = sum(neighbor_a1_values) / len(neighbor_ids)
            
            f.setAttribute(idx_a1, anomaly_1)
            f.setAttribute(idx_a2, anomaly_2)
            sink.addFeature(f, QgsFeatureSink.FastInsert)

        # ---------------------------------------------------------------------
        # STEP 5: Interpolation (GRASS GIS v.surf.rst)
        # ---------------------------------------------------------------------
        feedback.pushInfo("Interpolating anomaly surface via GRASS v.surf.rst...")
        output_resolution = 100.0
        
        try:
            # Capture the result of the GRASS processing tool
            grass_result = processing.run("grass:v.surf.rst", {
                'input': dest_id,
                'zcolumn': 'anomaly_2', 
                'tension': 40.0,         
                'smooth': '',            
                'zscale': 1.0,           
                '-c': False,             
                '-t': False,             
                '-d': False,             
                'elevation': parameters[self.OUTPUT_RASTER],
                'slope': None,
                'aspect': None,
                'pcurvature': None,
                'tcurvature': None,
                'mcurvature': None,
                'deviations': None,
                'cvdev': None,
                'tree': None,
                'overviews': None,
                'GRASS_REGION_PARAMETER': aoi_extent,
                'GRASS_REGION_CELLSIZE_PARAMETER': output_resolution,
                'GRASS_SNAP_TOLERANCE_PARAMETER': -1,
                'GRASS_MIN_AREA_PARAMETER': 0.0001
            }, context=context, feedback=feedback, is_child_algorithm=True)
            
        except QgsProcessingException as e:
            feedback.reportError(f"GRASS Interpolation failed: {str(e)}")
            return {self.OUTPUT_CENTROIDS: dest_id}

        # Attach the styling post-processor to the final raster output
        if context.willLoadLayerOnCompletion(self.OUTPUT_RASTER):
            context.layerToLoadOnCompletionDetails(self.OUTPUT_RASTER).setPostProcessor(EqualIntervalPostProcessor())

        # Return the explicit file path generated by GRASS
        return {
            self.OUTPUT_CENTROIDS: dest_id,
            self.OUTPUT_RASTER: grass_result['elevation']
        }