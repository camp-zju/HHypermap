import sys
import logging
import math
import json

from urlparse import urlparse
from django.conf import settings
from django.utils.html import strip_tags

from elasticsearch import Elasticsearch

from hypermap.aggregator.utils import mercator_to_llbbox

from hypermap.aggregator.solr import get_date


class ESHypermap(object):

    es_url = settings.SEARCH_URL
    es = Elasticsearch(hosts=[es_url])
    index_name = 'hypermap'
    logger = logging.getLogger("hypermap")

    def __init__(self):
        self.create_indices()
        super(ESHypermap, self).__init__()

    @staticmethod
    def good_coords(coords):
        """ passed a string array """
        if (len(coords) != 4):
            return False
        for coord in coords[0:3]:
            try:
                num = float(coord)
                if (math.isnan(num)):
                    return False
                if (math.isinf(num)):
                    return False
            except ValueError:
                return False
        return True

    @staticmethod
    def get_domain(url):
        urlParts = urlparse(url)
        hostname = urlParts.hostname
        if hostname == "localhost":
            return "Harvard"  # assumption
        return hostname

    @staticmethod
    def layer_to_es(layer):
        category = None
        username = None

        ESHypermap.logger.info("Elasticsearch: record to save: %s" % layer.id)

        try:
            bbox = [float(layer.bbox_x0), float(layer.bbox_y0), float(layer.bbox_x1), float(layer.bbox_y1)]
            for proj in layer.srs.values():
                if proj['code'] in ('102113', '102100'):
                    bbox = mercator_to_llbbox(bbox)
            if (ESHypermap.good_coords(bbox)) is False:
                print 'Elasticsearch: There are not valid coordinates for this layer ', layer.title
                ESHypermap.logger.error('Elasticsearch: There are not valid coordinates for layer id: %s' % layer.id)
                return False
            if (ESHypermap.good_coords(bbox)):
                minX = bbox[0]
                minY = bbox[1]
                maxX = bbox[2]
                maxY = bbox[3]
                if (minY > maxY):
                    minY, maxY = maxY, minY
                if (minX > maxX):
                    minX, maxX = maxX, minX
                centerY = (maxY + minY) / 2.0
                centerX = (maxX + minX) / 2.0
                halfWidth = (maxX - minX) / 2.0
                halfHeight = (maxY - minY) / 2.0
                area = (halfWidth * 2) * (halfHeight * 2)
                if (minX < -180):
                    minX = -180
                if (maxX > 180):
                    maxX = 180
                if (minY < -90):
                    minY = -90
                if (maxY > 90):
                    maxY = 90
                wkt = "ENVELOPE({:f},{:f},{:f},{:f})".format(minX, maxX, maxY, minY)
                domain = ESHypermap.get_domain(layer.service.url)
                if hasattr(layer, 'layerwm'):
                    category = layer.layerwm.category
                    username = layer.layerwm.username
                abstract = layer.abstract
                if abstract:
                    abstract = strip_tags(layer.abstract)
                else:
                    abstract = ''
                if layer.service.type == "WM":
                    originator = username
                else:
                    originator = domain
                # we need to remove the exising index in case there is already one
                # ESHypermap.es.delete('hypermap', 'layer', layer.id)
                # now we add the index
                es_record = {
                    "id": str(layer.id),
                    "type": 'Layer',
                    "layer_id": str(layer.id),
                    "name": layer.name,
                    "title": layer.title,
                    "layer_originator": originator,
                    "service_id": str(layer.service.id),
                    "service_type": layer.service.type,
                    "layer_category": category,
                    "layer_username": username,
                    "url": layer.url,
                    "reliability": layer.reliability,
                    "recent_reliability": layer.recent_reliability,
                    "last_status": layer.last_status,
                    "is_public": layer.is_public,
                    "availability": "Online",
                    "location": '{"layerInfoPage": "' + layer.get_absolute_url() + '"}',
                    "abstract": abstract,
                    "domain_name": layer.service.get_domain,
                    # "SrsProjectionCode": layer.srs.values_list('code', flat=True),
                    "min_y": minY,
                    "min_x": minX,
                    "max_x": maxY,
                    "max_y": maxX,
                    "area": area,
                    "bbox": wkt,
                    "srs": [srs.encode('utf-8') for srs in layer.srs.values_list('code', flat=True)],
                    "layer_geoshape": {
                        "type": "polygon",
                        "orientation": "clockwise",
                        "coordinates": [
                            [[minX, minY], [minX, maxY], [maxX, maxY], [maxX, minY], [minX, minY]]
                        ]
                    },
                }

                slugs = layer.get_catalogs_slugs()
                if slugs:
                    es_record["catalogs"] = slugs

                es_date, type = get_date(layer)
                if es_date is not None:
                    es_record['layer_date'] = es_date
                    es_record['layer_datetype'] = type
                ESHypermap.logger.info(es_record)
                ESHypermap.es.index(ESHypermap.index_name, 'layer', json.dumps(es_record), id=layer.id)
                ESHypermap.logger.info("Elasticsearch: record saved for layer with id: %s" % layer.id)
                return True, None
        except Exception:
            ESHypermap.logger.error(sys.exc_info())
            ESHypermap.logger.error("Elasticsearch: Error saving record for layer with id: %s - %s"
                                    % (layer.id, sys.exc_info()[1]))
            return False, sys.exc_info()[1]

    @staticmethod
    def clear_es():
        """Clear all indexes in the es core"""
        ESHypermap.es.indices.delete(ESHypermap.index_name, ignore=[400, 404])
        print 'Elasticsearch: Index cleared'

    @staticmethod
    def create_indices():
        """Create ES core indices """
        # TODO: enable auto_create_index in the ES nodes to make this implicit.
        # https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-index_.html#index-creation
        # http://support.searchly.com/customer/en/portal/questions/16312889-is-automatic-index-creation-disabled-?new=16312889
        mapping = {
            "mappings": {
                "layers": {
                    "properties": {
                        "geoshape": {
                            "type": "geo_shape",
                            "tree": "quadtree",
                            "precision": "1m"
                        }
                    }
                }
            }
        }
        ESHypermap.es.indices.create(ESHypermap.index_name, ignore=[400, 404], body=mapping)